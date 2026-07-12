"""hwpx 섹션 XML → TextNode 목록 파서.

섹션 XML(Contents/section*.xml)을 순회하여 편집 대상이 되는
문단(body_text)·표 셀(table_cell) 노드를 추출한다.
노드의 t_elems는 원본 트리의 hp:t Element 참조이므로,
이후 apply_edits 단계에서 텍스트만 부분 교체해 서식을 보존할 수 있다.

노드 순번(id) 규칙 — T2의 HTML 변환기 `_build_node_id_maps`와 반드시 일치해야 한다:

1. 섹션 루트(hs:sec)의 **직계 자식** hp:p만 문서 순서대로 순회한다.
2. 각 문단에서 먼저, 문단의 직계 자식 hp:run들의 **직계 자식** hp:tbl을
   문서 순서대로 처리한다. 즉 표 셀 노드들이 그 표를 담은 문단의
   body_text 노드보다 먼저 id를 받는다.
   최상위 표(직계 run 아래 tbl)마다 table_idx가 0부터 1씩 증가한다.
3. 표 처리: hp:tbl의 직계 hp:tr → 직계 hp:tc를 문서 순서로 순회한다.
   - 셀 안(자손 어디든)에 hp:tbl(중첩 표)이 있으면 그 **외부 셀 자체는
     노드로 만들지 않고**, 셀 내 최상위 중첩 표들을 문서 순서대로
     같은 table_idx로 재귀 처리한다(중첩 표의 셀들만 노드화).
   - 중첩 표가 없는 셀은 텍스트 유무와 무관하게 table_cell 노드가 된다.
4. 표 처리가 끝난 뒤, 문단에 run이 하나 이상 있으면(collect_runs_and_texts
   기준, 하위 subList·중첩 p의 run 포함, 표 내부는 제외) body_text 노드를
   만든다. run이 전혀 없는 문단은 노드화하지 않는다.
5. 노드 id는 위 순서대로 섹션 내 0부터 시작하는 순번이다.
"""
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import HWP_UNITS_PER_MM, TextNode
from .xml_utils import ns, register_namespaces, tag

# NOTE: style_mapper 연동 훅 — 스타일 해석(charPr/paraPr/borderFill)이 필요해지면
# 여기서 header.xml 기반 StyleMaps를 받아 run 요소들로부터 스타일을 해석한다.
# Phase 4 T1 범위에서는 제외.


def collect_runs_and_texts(elem: ET.Element) -> tuple[list[ET.Element], list[ET.Element]]:
    """문단(또는 셀) 요소 아래의 hp:run 목록과 hp:t 목록을 문서 순서로 수집한다.

    - 직계 자식 hp:tbl은 건너뛴다(표 내부 텍스트는 셀 노드가 담당).
    - hp:run을 만나면 run의 직계 자식 hp:t만 수집한다
      (run 내부의 tbl 등 다른 객체로는 들어가지 않는다).
    - hp:p·hp:subList·그 외 컨테이너 요소는 재귀 탐색한다.
    """
    runs: list[ET.Element] = []
    t_elems: list[ET.Element] = []

    for child in elem:
        ctag = tag(child)
        if ctag == "tbl":
            continue
        if ctag == "run":
            runs.append(child)
            for gc in child:
                if tag(gc) == "t":
                    t_elems.append(gc)
        else:
            sub_runs, sub_ts = collect_runs_and_texts(child)
            runs.extend(sub_runs)
            t_elems.extend(sub_ts)

    return runs, t_elems


def _find_toplevel_tables(elem: ET.Element) -> list[ET.Element]:
    """elem 자손 중 다른 tbl 안에 포함되지 않은 최상위 hp:tbl들을 문서 순서로 반환한다."""
    found: list[ET.Element] = []

    def _walk(e: ET.Element) -> None:
        for child in e:
            if tag(child) == "tbl":
                found.append(child)  # 더 깊은 중첩은 재귀 처리 시 다시 발견되므로 진입하지 않음
            else:
                _walk(child)

    _walk(elem)
    return found


def _attr_int(elem: ET.Element, key_substr: str, default: int) -> int:
    """속성 키에 key_substr가 포함된 속성값을 int로 읽는다(네임스페이스 프리픽스 무시)."""
    for k, v in elem.attrib.items():
        if key_substr in k:
            try:
                return int(v)
            except ValueError:
                return default
    return default


def parse_section(
    section_path: str | Path,
) -> tuple[list[TextNode], ET.ElementTree, dict[ET.Element, ET.Element], str]:
    """섹션 XML을 파싱해 (노드 목록, ET 트리, 부모 맵, t 네임스페이스)를 반환한다.

    - nodes: 모듈 docstring의 순번 규칙에 따른 TextNode 목록
    - tree: 원본 ElementTree (apply_edits 후 저장용)
    - parent_map: {자식 Element: 부모 Element} (요소 삽입·삭제 시 사용)
    - t_ns: hp:t 요소의 "{URI}" 네임스페이스 접두부 (새 t 요소 생성용)
    """
    section_path = str(section_path)
    register_namespaces(section_path)
    tree = ET.parse(section_path)
    root = tree.getroot()

    nodes: list[TextNode] = []
    next_id = [0]
    tbl_counter = [0]
    parent_map = {c: p for p in root.iter() for c in p}

    # 새 hp:t 생성 시 쓸 네임스페이스: 문서 내 첫 t 요소에서 추출
    t_ns = ""
    for elem in root.iter():
        if tag(elem) == "t":
            t_ns = ns(elem)
            break

    def _joined_text(t_elems: list[ET.Element]) -> str:
        return "".join((t.text or "") for t in t_elems)

    def _process_table(tbl_elem: ET.Element, tbl_idx: int) -> None:
        for tr in tbl_elem:
            if tag(tr) != "tr":
                continue
            for tc in tr:
                if tag(tc) != "tc":
                    continue

                # 중첩 표가 있는 셀: 외부 셀은 건너뛰고 내부 표 셀만 노드화
                nested = _find_toplevel_tables(tc)
                if nested:
                    for nested_tbl in nested:
                        _process_table(nested_tbl, tbl_idx)
                    continue

                row = col = -1
                cell_width = cell_height = 0
                col_span = row_span = 1
                for cc in tc:
                    cctag = tag(cc)
                    if cctag == "cellAddr":
                        col = _attr_int(cc, "colAddr", -1)
                        row = _attr_int(cc, "rowAddr", -1)
                    elif cctag == "cellSz":
                        cell_width = _attr_int(cc, "width", 0)
                        cell_height = _attr_int(cc, "height", 0)
                    elif cctag == "cellSpan":
                        col_span = _attr_int(cc, "colSpan", 1)
                        row_span = _attr_int(cc, "rowSpan", 1)

                _runs, t_elems = collect_runs_and_texts(tc)
                raw_text = _joined_text(t_elems)
                nodes.append(TextNode(
                    id=next_id[0], type="table_cell",
                    text=raw_text.strip(), raw_text=raw_text,
                    table_idx=tbl_idx, row=row, col=col,
                    cell_col_span=col_span, cell_row_span=row_span,
                    cell_width_mm=round(cell_width / HWP_UNITS_PER_MM) if cell_width > 0 else 0,
                    cell_height_mm=round(cell_height / HWP_UNITS_PER_MM) if cell_height > 0 else 0,
                    t_elems=t_elems,
                    elem=tc,
                ))
                next_id[0] += 1

    def _process_paragraph(p_elem: ET.Element) -> None:
        # 1) 문단 직계 run 아래의 표들을 먼저 처리 (표 셀이 문단보다 먼저 id를 받음)
        for run in p_elem:
            if tag(run) != "run":
                continue
            for child in run:
                if tag(child) == "tbl":
                    _process_table(child, tbl_counter[0])
                    tbl_counter[0] += 1

        # 2) run이 있는 문단만 body_text 노드화
        runs, t_elems = collect_runs_and_texts(p_elem)
        if not runs:
            return
        raw_text = _joined_text(t_elems)
        nodes.append(TextNode(
            id=next_id[0], type="body_text",
            text=raw_text.strip(), raw_text=raw_text,
            t_elems=t_elems,
            elem=p_elem,
        ))
        next_id[0] += 1

    for child in root:
        if tag(child) == "p":
            _process_paragraph(child)

    return nodes, tree, parent_map, t_ns
