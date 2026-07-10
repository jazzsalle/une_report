"""edits.py 테스트: id 정규화, apply_edits 왕복(적용·skip·불변성)."""
from pathlib import Path

import pytest

from app.core.hwpx import (
    apply_edits,
    extract_hwpx,
    find_section_files,
    normalize_edit_id,
    parse_section,
    validate_hwpx,
)


class TestNormalizeEditId:
    @pytest.mark.parametrize("raw, expected", [
        (12, 12),
        (0, 0),
        ("12", 12),
        (" 7 ", 7),
        ("p-0012", 12),       # 접두어+제로패딩
        ("node-3", 3),
        ("p-0012 ", 12),
    ])
    def test_accepts(self, raw, expected):
        assert normalize_edit_id(raw) == expected

    @pytest.mark.parametrize("raw", [-1, "abc", "", None, 3.5, True, "p-"])
    def test_rejects(self, raw):
        with pytest.raises(ValueError):
            normalize_edit_id(raw)


def _global_nodes(hwpx_path: Path, work_dir: Path) -> dict[int, object]:
    """{전역 id: TextNode} 매핑을 만든다(섹션 offset 누적)."""
    extract_dir = work_dir / f"nodes_{hwpx_path.stem}"
    extract_hwpx(hwpx_path, extract_dir)
    out: dict[int, object] = {}
    offset = 0
    for sf in find_section_files(extract_dir):
        nodes, _tree, _pm, _ns = parse_section(sf)
        for n in nodes:
            out[offset + n.id] = n
        offset += len(nodes)
    return out


def _find_gid(nodes: dict[int, object], contains: str) -> int:
    """텍스트에 contains가 포함된 첫 노드의 전역 id를 찾는다."""
    for gid in sorted(nodes):
        if contains in nodes[gid].text:
            return gid
    raise AssertionError(f"{contains!r}를 포함한 노드가 없음")


class TestApplyEdits:
    def test_edit_roundtrip_and_isolation(self, demo_form_hwpx, tmp_path):
        """문단·표 셀 편집 → 출력 검증 통과 → 재파싱 시 새 텍스트 반영,
        나머지 노드는 문자 하나도 변하지 않아야 한다."""
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        para_id = _find_gid(before, "[기관명]")       # body_text
        cell_id = _find_gid(before, "TBD")            # table_cell

        out = tmp_path / "edited.hwpx"
        result = apply_edits(demo_form_hwpx, [
            {"id": para_id, "new_text": "수립 기관: 서울특별시 재난안전대책본부"},
            {"id": cell_id, "new_text": "02-1234-5678"},
        ], out)

        assert sorted(result.applied_ids) == sorted([para_id, cell_id])
        assert result.skipped_ids == []
        assert result.output_path == str(out)
        assert result.section_snapshots  # 편집된 섹션 스냅샷이 남아야 함

        validation = validate_hwpx(out)
        assert validation.ok, validation.errors

        after = _global_nodes(out, tmp_path / "after")
        assert set(after) == set(before), "편집 후에도 노드 id 체계가 보존돼야 함"
        assert after[para_id].text == "수립 기관: 서울특별시 재난안전대책본부"
        assert after[cell_id].text == "02-1234-5678"

        # 편집하지 않은 모든 노드의 원문(raw_text)은 불변
        for gid in before:
            if gid in (para_id, cell_id):
                continue
            assert after[gid].raw_text == before[gid].raw_text, f"id={gid} 오염"

    def test_same_text_is_skipped(self, demo_form_hwpx, tmp_path):
        """원본과 실질 동일(공백 무시)한 new_text는 skip되고 XML은 무변경."""
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        gid = _find_gid(before, "[기관명]")
        out = tmp_path / "same.hwpx"
        result = apply_edits(
            demo_form_hwpx, [{"id": gid, "new_text": before[gid].raw_text}], out,
        )
        assert result.applied_ids == []
        assert result.skipped_ids == [gid]
        assert result.section_snapshots == []  # 변경된 섹션 없음
        assert validate_hwpx(out).ok

    def test_unknown_id_is_skipped(self, demo_form_hwpx, tmp_path):
        """존재하지 않는 전역 id는 에러 없이 skipped_ids로 보고."""
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        missing = max(before) + 1000
        out = tmp_path / "missing.hwpx"
        result = apply_edits(demo_form_hwpx, [{"id": missing, "new_text": "x"}], out)
        assert result.applied_ids == []
        assert result.skipped_ids == [missing]
        assert validate_hwpx(out).ok

    def test_prefixed_string_id_normalized(self, demo_form_hwpx, tmp_path):
        """"p-0012" 형식 id도 정수 id와 동일하게 적용돼야 한다."""
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        gid = _find_gid(before, "TBD")
        out = tmp_path / "prefixed.hwpx"
        result = apply_edits(
            demo_form_hwpx, [{"id": f"p-{gid:04d}", "new_text": "031-000-0000"}], out,
        )
        assert result.applied_ids == [gid]
        after = _global_nodes(out, tmp_path / "after")
        assert after[gid].text == "031-000-0000"

    def test_newlines_flattened_to_single_line(self, demo_form_hwpx, tmp_path):
        """개행 포함 new_text는 1차 범위에서 공백으로 합쳐져 들어간다."""
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        gid = _find_gid(before, "본 문서는")
        out = tmp_path / "nl.hwpx"
        apply_edits(demo_form_hwpx, [{"id": gid, "new_text": "첫 줄\n둘째 줄"}], out)
        after = _global_nodes(out, tmp_path / "after")
        assert after[gid].text == "첫 줄 둘째 줄"

    def test_duplicate_id_last_wins(self, demo_form_hwpx, tmp_path):
        before = _global_nodes(demo_form_hwpx, tmp_path / "before")
        gid = _find_gid(before, "[기관명]")
        out = tmp_path / "dup.hwpx"
        apply_edits(demo_form_hwpx, [
            {"id": gid, "new_text": "먼저"},
            {"id": gid, "new_text": "수립 기관: 최종값"},
        ], out)
        after = _global_nodes(out, tmp_path / "after")
        assert after[gid].text == "수립 기관: 최종값"

    def test_edit_on_real_sample(self, report_table_hwpx, tmp_path):
        """실제 도구로 만든 샘플에서도 편집 왕복이 동작해야 한다."""
        before = _global_nodes(report_table_hwpx, tmp_path / "before")
        cells = [gid for gid, n in before.items() if n.type == "table_cell" and n.text]
        assert cells
        target = cells[0]
        out = tmp_path / "real_edited.hwpx"
        result = apply_edits(report_table_hwpx, [{"id": target, "new_text": "수정된 값"}], out)
        assert result.applied_ids == [target]
        assert validate_hwpx(out).ok
        after = _global_nodes(out, tmp_path / "after")
        assert after[target].text == "수정된 값"
