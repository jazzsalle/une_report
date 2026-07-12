"""가이드 감지(B1)·표 구조 노출(B2)·fill 대상/청킹(B3) 단위 테스트.

가이드 스타일 hwpx 픽스처는 demo_form의 header.xml에 '기울임+파랑' charPr를
추가하고 첫 문단 run이 이를 참조하도록 후처리해 만든다 (합성 재현 —
실물 서식1 검증은 test_real_form_regression.py).
"""
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.core.hwpx import (
    extract_hwpx,
    find_header_file,
    find_section_files,
    guide_char_pr_ids,
    parse_section,
)
from app.core.hwpx.package import repack_hwpx
from app.core.hwpx.styles import _is_blueish
from app.core.hwpx.xml_utils import register_namespaces, tag
from app.services.orchestrator import Orchestrator
from app.services.prompts import format_nodes, format_placeholders

GUIDE_CHAR_ID = "9901"


# ---------------------------------------------------------------------------
# styles.py 단위
# ---------------------------------------------------------------------------

class TestBlueish:
    @pytest.mark.parametrize("color", ["#0000FF", "#1F3FBF", "#3355CC"])
    def test_blue_colors(self, color):
        assert _is_blueish(color)

    @pytest.mark.parametrize("color", [
        "#000000", "#FF0000", "#00FF00", "#FFFFFF", "#777777",
        None, "", "blue", "#GGHHII", "#00F",
    ])
    def test_non_blue_or_invalid(self, color):
        assert not _is_blueish(color)


# ---------------------------------------------------------------------------
# 가이드 픽스처 — demo_form에 가이드 charPr·run 주입
# ---------------------------------------------------------------------------

def _make_guided_hwpx(src: Path, work_dir: Path, out: Path) -> Path:
    """demo_form 사본에 기울임+파랑 charPr(GUIDE_CHAR_ID)를 추가하고
    '[기관명]' 문단의 run이 이를 참조하게 만든다."""
    extract_dir = work_dir / "guided_src"
    compress_info, file_order = extract_hwpx(src, extract_dir)

    # 1) header.xml: 기존 charPr를 복제해 기울임+파랑으로 변형
    header = find_header_file(extract_dir)
    assert header is not None
    register_namespaces(header)
    htree = ET.parse(header)
    char_prs = [e for e in htree.getroot().iter() if tag(e) == "charPr"]
    assert char_prs, "header.xml에 charPr가 있어야 한다"
    base = char_prs[0]
    import copy
    guide_pr = copy.deepcopy(base)
    guide_pr.set("id", GUIDE_CHAR_ID)
    for k in list(guide_pr.attrib):
        if k.split("}")[-1] == "textColor":
            del guide_pr.attrib[k]
    guide_pr.set("textColor", "#0000FF")
    ns = base.tag.split("}")[0] + "}" if base.tag.startswith("{") else ""
    ET.SubElement(guide_pr, f"{ns}italic")
    parent = next(p for p in htree.getroot().iter() if base in list(p))
    parent.append(guide_pr)
    htree.write(header, xml_declaration=True, encoding="utf-8")

    # 2) 섹션: '[기관명]' 텍스트를 가진 run이 가이드 charPr를 참조
    sf = find_section_files(extract_dir)[0]
    register_namespaces(sf)
    stree = ET.parse(sf)
    changed = False
    for run in stree.getroot().iter():
        if tag(run) != "run":
            continue
        texts = "".join((t.text or "") for t in run if tag(t) == "t")
        if "[기관명]" in texts:
            run.set("charPrIDRef", GUIDE_CHAR_ID)
            changed = True
    assert changed, "'[기관명]' run을 찾아 가이드 스타일을 입혔어야 한다"
    stree.write(sf, xml_declaration=True, encoding="utf-8")

    repack_hwpx(extract_dir, out, compress_info, file_order)
    return out


@pytest.fixture
def guided_hwpx(demo_form_hwpx, tmp_path) -> Path:
    return _make_guided_hwpx(demo_form_hwpx, tmp_path, tmp_path / "guided.hwpx")


class TestGuideDetection:
    def test_guide_char_pr_ids_from_header(self, guided_hwpx, tmp_path):
        extract_dir = tmp_path / "g1"
        extract_hwpx(guided_hwpx, extract_dir)
        ids = guide_char_pr_ids(find_header_file(extract_dir))
        assert ids == {GUIDE_CHAR_ID}

    def test_parse_section_fills_guide_text(self, guided_hwpx, tmp_path):
        extract_dir = tmp_path / "g2"
        extract_hwpx(guided_hwpx, extract_dir)
        ids = guide_char_pr_ids(find_header_file(extract_dir))
        sf = find_section_files(extract_dir)[0]
        nodes, *_ = parse_section(sf, guide_char_ids=ids)
        guide_nodes = [n for n in nodes if n.guide_text]
        assert guide_nodes, "가이드 run을 담은 노드가 있어야 한다"
        assert any("[기관명]" in n.guide_text for n in guide_nodes)
        # guide_char_ids 없이 부르면 기존 동작 (guide_text 빈 문자열)
        nodes_plain, *_ = parse_section(sf)
        assert all(not n.guide_text for n in nodes_plain)

    def test_missing_header_returns_empty(self, tmp_path):
        assert guide_char_pr_ids(tmp_path / "없는파일.xml") == set()


class TestStorePlaceholderKinds:
    def test_get_placeholders_includes_guide_and_pattern(self, guided_hwpx, tmp_path):
        """DocumentStore.get_placeholders가 kind=guide 항목을 함께 낸다."""
        from app.db import database
        from app.services.document_store import DocumentStore

        db = database.connect(tmp_path / "t.db")
        database.init_db(db)
        now = database.now_iso()
        db.execute(
            "INSERT INTO users(account, user_name, created_at) VALUES(?,?,?)",
            ("guide-user", "guide-user", now),
        )
        user_id = db.execute("SELECT id FROM users").fetchone()["id"]
        store = DocumentStore(db, files_dir=tmp_path / "files")
        meta = store.create_document(user_id, "guided.hwpx", guided_hwpx.read_bytes())

        phs = store.get_placeholders(user_id, meta["document_id"])
        kinds = {p["kind"] for p in phs}
        assert kinds == {"pattern", "guide"}
        guide = next(p for p in phs if p["kind"] == "guide")
        assert "[기관명]" in guide["token"]
        # 같은 노드가 pattern으로도 잡힌다 ([기관명]은 regex 표식이기도 함)
        assert any(p["kind"] == "pattern" and p["token"] == "[기관명]" for p in phs)
        db.close()


# ---------------------------------------------------------------------------
# B2 — 표 그리드 직렬화 / B3 — prompt_nodes·fill 대상·청킹
# ---------------------------------------------------------------------------

def _cell(gid, table_idx, row, col, text=""):
    return {
        "id": gid, "text": text, "type": "cell",
        "table_idx": table_idx, "row": row, "col": col,
        "row_span": 1, "col_span": 1,
    }


def _para(gid, text):
    return {"id": gid, "text": text, "type": "para"}


class TestFormatNodesGrid:
    def test_tables_grouped_with_coordinates_and_empty_marker(self):
        nodes = [
            _para(0, "제목 문단"),
            _cell(1, 0, 0, 0, "사업명"),
            _cell(2, 0, 0, 1),          # 빈 값 칸
            _para(3, "맺음 문단"),
        ]
        text = format_nodes(nodes)
        lines = text.splitlines()
        assert lines[0] == "0 [para] 제목 문단"
        assert lines[1] == "[표 0]"
        assert lines[2] == "(0,0) 1: 사업명"
        assert lines[3] == "(0,1) 2: (빈 칸)"
        assert lines[4] == "3 [para] 맺음 문단"

    def test_plain_para_format_unchanged(self):
        assert format_nodes([_para(5, "본문")]) == "5 [para] 본문"

    def test_guide_placeholder_marked_as_instruction(self):
        text = format_placeholders([
            {"id": 1, "token": "[기관명]", "kind": "pattern"},
            {"id": 2, "token": "500자 내외 작성", "kind": "guide"},
        ])
        assert "1 [표식]: [기관명]" in text
        assert "2 [지시]: 500자 내외 작성" in text


class TestPromptNodesAndFillTargets:
    def test_prompt_nodes_drops_empty_without_selection(self):
        nodes = [_para(0, "내용"), _cell(1, 0, 0, 0, ""), _cell(2, 0, 0, 1, "라벨")]
        out = Orchestrator._prompt_nodes(nodes, [], selection=None)
        assert [n["id"] for n in out] == [0, 2]

    def test_prompt_nodes_keeps_placeholder_bearing_empty_node(self):
        nodes = [_para(0, "내용"), _cell(1, 0, 0, 0, "")]
        out = Orchestrator._prompt_nodes(
            nodes, [{"id": 1, "token": "지시문", "kind": "guide"}], selection=None
        )
        assert [n["id"] for n in out] == [0, 1]

    def test_prompt_nodes_keeps_all_with_selection(self):
        nodes = [_cell(1, 0, 0, 0, "")]
        assert Orchestrator._prompt_nodes(nodes, [], selection=[1]) == nodes

    def test_fill_chunks_pack_small_groups_into_one_call(self):
        """상한 안에 들면 표·본문 그룹을 한 청크로 묶는다 (호출 수 최소화)."""
        targets = (
            [_cell(i, 0, i, 0) for i in range(3)]        # 표 0
            + [_para(10, "본문")]
            + [_cell(20 + i, 1, i, 0) for i in range(3)]  # 표 1
        )
        chunks = Orchestrator._fill_chunks(targets, size=30)
        assert [[n["id"] for n in ch] for ch in chunks] == [
            [0, 1, 2, 10, 20, 21, 22],
        ]

    def test_fill_chunks_never_split_small_table_across_chunks(self):
        """상한을 넘길 때 표는 통째로 다음 청크로 넘어간다 (표 불분할)."""
        targets = (
            [_cell(i, 0, i, 0) for i in range(3)]         # 표 0 (3)
            + [_cell(20 + i, 1, i, 0) for i in range(3)]  # 표 1 (3)
        )
        chunks = Orchestrator._fill_chunks(targets, size=5)
        assert [[n["id"] for n in ch] for ch in chunks] == [
            [0, 1, 2], [20, 21, 22],
        ]

    def test_fill_chunks_split_large_table_by_size(self):
        targets = [_cell(i, 0, i, 0) for i in range(7)]
        chunks = Orchestrator._fill_chunks(targets, size=3)
        assert [len(c) for c in chunks] == [3, 3, 1]
        assert [n["id"] for n in chunks[0]] == [0, 1, 2]  # 행 순서 유지


class TestFillTargetExpansion:
    """B3 — fill 대상: 표식/가이드 노드 + 같은 표의 빈 셀 (다른 표·본문 제외)."""

    def test_empty_cells_of_placeholder_table_join_targets(self):
        import asyncio
        from tests.test_orchestrator import FakeBackend

        nodes = [
            _cell(0, 0, 0, 0, "[기관명]"),   # 표식 노드
            _cell(1, 0, 0, 1),               # 같은 표 빈 셀 → 대상 포함
            _cell(2, 1, 0, 0),               # 표식 없는 표의 빈 셀 → 제외
            _para(3, "일반 본문"),            # 표식 없음 → 제외
        ]
        backend = FakeBackend([
            '{"intent": "fill"}',
            '{"reply": "채움", "edits": [{"id": 0, "new_text": "기관: 서울시"},'
            ' {"id": 1, "new_text": "값"}]}',
        ])
        orch = Orchestrator(backend)
        result = asyncio.run(orch.run_turn(
            token="t", message="양식 채워줘", history=[],
            doc_nodes=nodes,
            placeholders=[{"id": 0, "token": "[기관명]", "kind": "pattern"}],
        ))
        assert result.intent == "fill"
        fill_prompt = backend.calls[1][0]
        assert "(0,1) 1: (빈 칸)" in fill_prompt   # 같은 표 빈 셀이 대상
        assert "[표 1]" not in fill_prompt          # 표식 없는 표는 제외
        assert "일반 본문" not in fill_prompt       # 표식 없는 본문 제외
        assert {e["id"] for e in result.edits} == {0, 1}
