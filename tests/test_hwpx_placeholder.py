"""placeholder.py·chunker.py 테스트: 표식 탐지, 잔존/해소 리포트, 표 불분할 청킹."""
from pathlib import Path

import pytest

from app.core.hwpx import (
    apply_edits,
    chunk_nodes,
    collect_placeholders,
    extract_hwpx,
    find_section_files,
    parse_section,
    verify_output,
)


def _first_section_nodes(hwpx_path: Path, work_dir: Path) -> list:
    """전 섹션 노드를 전역 id 순으로 이어붙여 반환한다(단일 목록)."""
    extract_dir = work_dir / f"ph_{hwpx_path.stem}"
    extract_hwpx(hwpx_path, extract_dir)
    all_nodes = []
    for sf in find_section_files(extract_dir):
        nodes, _tree, _pm, _ns = parse_section(sf)
        all_nodes.extend(nodes)
    return all_nodes


class TestCollectPlaceholders:
    def test_detects_all_four_pattern_types(self, demo_form_hwpx, tmp_path):
        """데모 양식에서 bracket/angle/date_stub/filler 4유형이 모두 탐지돼야 한다."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        hits = collect_placeholders(nodes)

        by_pattern: dict[str, set[str]] = {}
        for h in hits:
            by_pattern.setdefault(h.pattern, set()).add(h.text)

        assert "[기관명]" in by_pattern.get("bracket", set())
        assert "[주소 입력]" in by_pattern.get("bracket", set())
        assert "<담당자 이름>" in by_pattern.get("angle", set())
        assert {"YYYY", "MM월", "DD일"} <= by_pattern.get("date_stub", set())
        assert "TBD" in by_pattern.get("filler", set())
        assert any(t.startswith("○○") for t in by_pattern.get("filler", set()))

    def test_hit_ids_point_to_source_nodes(self, demo_form_hwpx, tmp_path):
        """hit.id로 원본 노드를 역참조하면 해당 표식이 실제로 들어 있어야 한다."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        by_id = {n.id: n for n in nodes}
        for hit in collect_placeholders(nodes):
            assert hit.text in by_id[hit.id].text

    def test_id_offset_shifts_ids(self, demo_form_hwpx, tmp_path):
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        base = collect_placeholders(nodes)
        shifted = collect_placeholders(nodes, id_offset=100)
        assert [h.id + 100 for h in base] == [h.id for h in shifted]

    def test_clean_nodes_have_no_hits(self, report_table_hwpx, tmp_path):
        """placeholder가 없는 일반 문서에서는 탐지 결과가 없어야 한다."""
        nodes = _first_section_nodes(report_table_hwpx, tmp_path)
        assert collect_placeholders(nodes) == []


class TestVerifyOutput:
    def test_unedited_form_reports_remaining(self, demo_form_hwpx):
        """편집 전 양식: 표식이 전부 remaining이고 resolved는 비어야 한다."""
        report = verify_output(demo_form_hwpx, expected_replaced={"[기관명]", "TBD"})
        remaining_texts = {h.text for h in report.remaining}
        assert "[기관명]" in remaining_texts
        assert "TBD" in remaining_texts
        assert report.resolved == []

    def test_partial_edit_splits_resolved_and_remaining(self, demo_form_hwpx, tmp_path):
        """일부만 채운 출력: 채운 표식은 resolved, 안 채운 표식은 remaining."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        org_id = next(n.id for n in nodes if "[기관명]" in n.text)
        tbd_id = next(n.id for n in nodes if "TBD" in n.text)

        out = tmp_path / "partial.hwpx"
        apply_edits(demo_form_hwpx, [
            {"id": org_id, "new_text": "수립 기관: 서울특별시"},
            {"id": tbd_id, "new_text": "02-1234-5678"},
        ], out)

        report = verify_output(
            out, expected_replaced={"[기관명]", "TBD", "<담당자 이름>"},
        )
        assert set(report.resolved) == {"[기관명]", "TBD"}
        remaining_texts = {h.text for h in report.remaining}
        assert "<담당자 이름>" in remaining_texts       # 안 채운 표식은 잔존
        assert "[기관명]" not in remaining_texts
        assert "TBD" not in remaining_texts
        # 안 채운 다른 유형(날짜 스텁 등)도 일반 패턴으로 계속 잡혀야 함
        assert any(h.pattern == "date_stub" for h in report.remaining)

    def test_expected_pattern_marks_surviving_original_text(self, demo_form_hwpx):
        """패턴에 안 걸리는 원본 의심 텍스트도 expected로 잔존 보고돼야 한다."""
        report = verify_output(demo_form_hwpx, expected_replaced={"수립 기관"})
        assert any(
            h.pattern == "expected" and h.text == "수립 기관" for h in report.remaining
        )


class TestChunkNodes:
    def test_table_cells_never_split(self, demo_form_hwpx, tmp_path):
        """max_nodes를 표 크기보다 작게 줘도 같은 표의 셀은 한 청크에 있어야 한다."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        table_sizes = {}
        for n in nodes:
            if n.type == "table_cell":
                table_sizes[n.table_idx] = table_sizes.get(n.table_idx, 0) + 1
        assert table_sizes and max(table_sizes.values()) >= 2

        chunks = chunk_nodes(nodes, max_nodes=2)  # 모든 표보다 작은 상한

        # 각 표의 셀 전체가 정확히 하나의 청크에만 등장
        for tbl_idx, size in table_sizes.items():
            holders = [
                ch for ch in chunks
                if any(n.type == "table_cell" and n.table_idx == tbl_idx for n in ch)
            ]
            assert len(holders) == 1, f"표 {tbl_idx}가 여러 청크로 분할됨"
            in_chunk = sum(
                1 for n in holders[0]
                if n.type == "table_cell" and n.table_idx == tbl_idx
            )
            assert in_chunk == size

    def test_order_and_completeness(self, demo_form_hwpx, tmp_path):
        """청크를 이어붙이면 원본 노드 순서가 그대로 복원돼야 한다."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        chunks = chunk_nodes(nodes, max_nodes=3)
        flattened = [n for ch in chunks for n in ch]
        assert [n.id for n in flattened] == [n.id for n in nodes]

    def test_chunk_size_limit_except_atomic_tables(self, demo_form_hwpx, tmp_path):
        """청크 크기는 max(max_nodes, 가장 큰 표 크기)를 넘지 않아야 한다."""
        nodes = _first_section_nodes(demo_form_hwpx, tmp_path)
        table_sizes: dict[int, int] = {}
        for n in nodes:
            if n.type == "table_cell":
                table_sizes[n.table_idx] = table_sizes.get(n.table_idx, 0) + 1
        max_table = max(table_sizes.values())
        for max_nodes in (1, 2, 3, 30):
            for ch in chunk_nodes(nodes, max_nodes=max_nodes):
                assert len(ch) <= max(max_nodes, max_table)

    def test_empty_and_invalid_inputs(self):
        assert chunk_nodes([], max_nodes=5) == []
        with pytest.raises(ValueError):
            chunk_nodes([], max_nodes=0)


class TestBracketExclusions:
    """bracket 오탐 제외 (A3) — 캡션 번호·체크박스는 placeholder가 아니다."""

    @staticmethod
    def _node(nid: int, text: str):
        from app.core.hwpx import TextNode
        return TextNode(id=nid, type="body_text", text=text, raw_text=text)

    def test_caption_numbers_excluded(self):
        nodes = [
            self._node(0, "[그림 1] 시설 배치도"),
            self._node(1, "[표 12] 비상연락망"),
            self._node(2, "[사진 3]"),
            self._node(3, "[별표 1] 관련 규정"),
            self._node(4, "[붙임 2] 서식"),
        ]
        assert collect_placeholders(nodes) == []

    def test_checkboxes_excluded(self):
        """실양식 실측: [ ]·[√] 체크박스가 placeholder로 오탐되지 않아야 한다."""
        nodes = [
            self._node(0, "동의함 [ ] 동의하지 않음 [  ]"),
            self._node(1, "확인 [√]"),
            self._node(2, "[V] 완료"),
            self._node(3, "[○] 해당"),
            self._node(4, "[x]"),
        ]
        assert collect_placeholders(nodes) == []

    def test_normal_brackets_still_detected(self):
        """정상 채움 표식은 계속 탐지된다 (숫자 없는 캡션 유사어 포함)."""
        nodes = [
            self._node(0, "[기관명]"),
            self._node(1, "[그림 설명 입력]"),   # '그림'으로 시작해도 번호가 아니면 유지
            self._node(2, "[주소 입력]"),
        ]
        hits = collect_placeholders(nodes)
        assert {h.text for h in hits} == {"[기관명]", "[그림 설명 입력]", "[주소 입력]"}
        assert all(h.pattern == "bracket" for h in hits)
