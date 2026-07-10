"""hwpx → docx 단순 변환기 (M2-1, 1차 범위).

M1 파서의 TextNode 목록(문단·표 셀)을 python-docx로 재구성한다.
목표는 "내용 전달용" 사본이다 — 글자 서식·이미지·셀 병합·페이지 설정은
범위 외이며, 문단 텍스트와 표 격자(행·열 주소)만 보존한다.

노드 순번 규칙(parser.parse_section docstring)상 표 셀 노드들이 그 표를 담은
문단의 body_text 노드보다 먼저 나오므로, 연속한 같은 table_idx의 셀들을
버퍼링했다가 table_idx가 바뀌거나 문단을 만나면 표 하나로 내보낸다.
(원문에서 표가 문단 중간에 끼어 있어도 docx에서는 "표 → 문단" 순서가 된다.)
"""
import tempfile
from pathlib import Path

from docx import Document

from app.core.hwpx import TextNode, extract_hwpx, find_section_files, parse_section

__all__ = ["hwpx_to_docx"]


def _emit_table(doc: Document, cells: list[TextNode]) -> None:
    """같은 table_idx의 셀 노드들을 docx 표 하나로 내보낸다.

    행·열 주소(cellAddr)가 모두 유효하면 격자로 배치하고,
    하나라도 없으면(비정상 문서) 셀 텍스트를 1열 표로 나열한다.
    """
    if not cells:
        return
    if all(c.row >= 0 and c.col >= 0 for c in cells):
        n_rows = max(c.row for c in cells) + 1
        n_cols = max(c.col for c in cells) + 1
        table = doc.add_table(rows=n_rows, cols=n_cols)
        for c in cells:
            table.cell(c.row, c.col).text = c.text
    else:
        table = doc.add_table(rows=len(cells), cols=1)
        for i, c in enumerate(cells):
            table.cell(i, 0).text = c.text
    table.style = "Table Grid"


def _emit_nodes(doc: Document, nodes: list[TextNode]) -> None:
    """섹션 노드 목록을 문서 순서대로 문단·표로 내보낸다."""
    pending: list[TextNode] = []  # 아직 내보내지 않은 현재 표의 셀들
    for node in nodes:
        if node.type == "table_cell":
            if pending and pending[0].table_idx != node.table_idx:
                _emit_table(doc, pending)
                pending = []
            pending.append(node)
        else:  # body_text
            if pending:
                _emit_table(doc, pending)
                pending = []
            doc.add_paragraph(node.text)
    if pending:
        _emit_table(doc, pending)


def hwpx_to_docx(hwpx_path: str | Path, output_path: str | Path) -> Path:
    """hwpx의 텍스트·표를 docx로 재구성해 output_path에 저장한다."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    with tempfile.TemporaryDirectory(prefix="hwpx2docx_", ignore_cleanup_errors=True) as tmp:
        extract_dir = Path(tmp) / "hwpx"
        extract_hwpx(hwpx_path, extract_dir)
        for sf in find_section_files(extract_dir):
            nodes, _tree, _parent_map, _t_ns = parse_section(sf)
            _emit_nodes(doc, nodes)
    doc.save(str(output_path))
    return output_path
