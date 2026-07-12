"""서식1 사업계획서 실양식 회귀 테스트 (B4 — 하드 게이트).

`ouputs/[서식1] 사업계획서(신청용).hwpx`를 실측 픽스처로 삼아, 합성
템플릿에서 재현되지 않던 두 병목(빈 셀 hp:t 부재, 파란 기울임체 가이드)이
회귀하지 않는지 지킨다. 파일이 없으면 skip (대용량 실양식은 커밋 대상 아님).

기대값은 2026-07-12 실측 고정치:
- 노드 5,386 (섹션 2) / hp:t 없는 노드 3,687 (68%)
- 가이드 charPr 61종
"""
import tempfile
from pathlib import Path

import pytest

from app.core.hwpx import (
    apply_edits,
    extract_hwpx,
    find_header_file,
    find_section_files,
    guide_char_pr_ids,
    parse_section,
    validate_hwpx,
)

FORM1 = Path(__file__).resolve().parent.parent / "ouputs" / "[서식1] 사업계획서(신청용).hwpx"

pytestmark = pytest.mark.skipif(
    not FORM1.is_file(), reason="서식1 실양식 없음 (ouputs/…hwpx)"
)


def _parse_all(hwpx_path: Path, extract_dir: Path):
    """(전역 노드 dict, 가이드 charPr id 집합) — 섹션 offset 누적."""
    extract_hwpx(hwpx_path, extract_dir)
    guide_ids = guide_char_pr_ids(find_header_file(extract_dir))
    out = {}
    offset = 0
    for sf in find_section_files(extract_dir):
        nodes, *_ = parse_section(sf, guide_char_ids=guide_ids)
        for n in nodes:
            out[offset + n.id] = n
        offset += len(nodes)
    return out, guide_ids


def test_form1_hard_gates(tmp_path):
    """노드 수·빈 노드 수·가이드 charPr 수가 실측 고정치와 일치해야 한다."""
    nodes, guide_ids = _parse_all(FORM1, tmp_path / "form1")
    assert len(nodes) == 5386
    empty = [gid for gid, n in nodes.items() if not n.t_elems]
    assert len(empty) == 3687
    assert len(guide_ids) == 61
    guide_nodes = [n for n in nodes.values() if n.guide_text]
    assert len(guide_nodes) >= 100, "가이드 문구 노드가 대량 탐지돼야 한다"
    # 대표 가이드 문구 실존 확인 (양식 첫 줄 안내문)
    assert any("작성 가이드" in n.guide_text for n in nodes.values())


def test_form1_empty_cell_edit_roundtrip(tmp_path):
    """hp:t 없는 빈 셀 2개에 apply_edits → 적용·검증·재파싱 반영 (A1 게이트)."""
    nodes, _ = _parse_all(FORM1, tmp_path / "before")
    empty_cells = sorted(
        gid for gid, n in nodes.items()
        if n.type == "table_cell" and not n.t_elems
    )[:2]
    assert len(empty_cells) == 2

    out = tmp_path / "form1_filled.hwpx"
    result = apply_edits(FORM1, [
        {"id": gid, "new_text": f"회귀 채움 {gid}"} for gid in empty_cells
    ], out)
    assert sorted(result.applied_ids) == empty_cells
    assert result.skipped_ids == []
    assert validate_hwpx(out).ok

    with tempfile.TemporaryDirectory() as tmp:
        after, _ = _parse_all(out, Path(tmp) / "after")
    assert len(after) == len(nodes), "편집 후에도 노드 수(id 체계) 불변"
    for gid in empty_cells:
        assert after[gid].text == f"회귀 채움 {gid}"
