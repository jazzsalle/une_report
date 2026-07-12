"""edits.py 테스트: id 정규화, apply_edits 왕복(적용·skip·불변성), 빈 노드 채움."""
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.core.hwpx import (
    apply_edits,
    extract_hwpx,
    find_section_files,
    normalize_edit_id,
    parse_section,
    validate_hwpx,
)
from app.core.hwpx.package import repack_hwpx
from app.core.hwpx.xml_utils import register_namespaces, tag


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


# ---------------------------------------------------------------------------
# 빈 노드(hp:t 없음) 채움 — A1 (실양식 빈 셀 구조 재현)
# ---------------------------------------------------------------------------

def _make_hancom_style_empty_cells(src: Path, work_dir: Path, out: Path) -> Path:
    """데모 양식의 표 셀 두 개를 '한컴식 빈 셀'로 바꿔 재패키징한다.

    - 첫 셀: hp:t만 제거하고 run은 유지 (실양식 실측상 지배적 구조)
    - 둘째 셀: run째 제거 (희귀하지만 가능한 구조)
    """
    extract_dir = work_dir / "empty_src"
    compress_info, file_order = extract_hwpx(src, extract_dir)
    sf = find_section_files(extract_dir)[0]
    register_namespaces(sf)
    tree = ET.parse(sf)
    tcs = [e for e in tree.getroot().iter() if tag(e) == "tc"]
    assert len(tcs) >= 2, "표 셀이 2개 이상인 양식이어야 한다"

    # 셀 0: run 유지 + t 제거
    for run in [e for e in tcs[0].iter() if tag(e) == "run"]:
        for t in [c for c in run if tag(c) == "t"]:
            run.remove(t)

    # 셀 1: run째 제거
    def _strip_runs(elem: ET.Element) -> None:
        for child in list(elem):
            if tag(child) == "run":
                elem.remove(child)
            else:
                _strip_runs(child)

    _strip_runs(tcs[1])

    tree.write(sf, xml_declaration=True, encoding="utf-8")
    repack_hwpx(extract_dir, out, compress_info, file_order)
    return out


class TestEmptyNodeFill:
    @pytest.fixture
    def empty_cell_hwpx(self, demo_form_hwpx, tmp_path) -> Path:
        return _make_hancom_style_empty_cells(
            demo_form_hwpx, tmp_path, tmp_path / "empty_cells.hwpx"
        )

    def _empty_cell_gids(self, nodes: dict[int, object]) -> list[int]:
        return [
            gid for gid in sorted(nodes)
            if nodes[gid].type == "table_cell" and not nodes[gid].t_elems
        ]

    def test_fill_empty_cells_roundtrip(self, empty_cell_hwpx, tmp_path):
        """run만 있는 셀·run조차 없는 셀 모두 hp:t가 생성돼 채워진다."""
        before = _global_nodes(empty_cell_hwpx, tmp_path / "before")
        empty_gids = self._empty_cell_gids(before)
        assert len(empty_gids) >= 2, "한컴식 빈 셀이 2개 만들어져 있어야 한다"
        run_only, no_run = empty_gids[0], empty_gids[1]

        out = tmp_path / "filled.hwpx"
        result = apply_edits(empty_cell_hwpx, [
            {"id": run_only, "new_text": "기존 run에 채움"},
            {"id": no_run, "new_text": "생성 run에 채움"},
        ], out)

        assert sorted(result.applied_ids) == sorted([run_only, no_run])
        assert result.skipped_ids == []
        assert validate_hwpx(out).ok

        after = _global_nodes(out, tmp_path / "after")
        assert after[run_only].text == "기존 run에 채움"
        assert after[no_run].text == "생성 run에 채움"
        # 노드 수·순번(id 체계)이 삽입으로 흔들리지 않아야 한다
        assert sorted(after) == sorted(before)
        # 나머지 노드 원문 보존
        for gid in before:
            if gid in (run_only, no_run):
                continue
            assert after[gid].raw_text == before[gid].raw_text, f"id={gid} 오염"

    def test_created_run_inherits_char_pr_ref(self, empty_cell_hwpx, tmp_path):
        """run째 없던 셀에 생성된 run은 섹션의 charPrIDRef를 복제한다."""
        before = _global_nodes(empty_cell_hwpx, tmp_path / "before")
        no_run = self._empty_cell_gids(before)[1]
        out = tmp_path / "charpr.hwpx"
        apply_edits(empty_cell_hwpx, [{"id": no_run, "new_text": "서식 확인"}], out)

        after = _global_nodes(out, tmp_path / "after")
        # 새 t의 부모 run이 charPrIDRef를 갖는다 (섹션 첫 run에서 복제)
        extract_dir = tmp_path / "verify_charpr"
        extract_hwpx(out, extract_dir)
        sf = find_section_files(extract_dir)[0]
        nodes, tree, parent_map, _ns = parse_section(sf)
        node = next(n for n in nodes if n.id == no_run and n.type == "table_cell")
        assert node.t_elems, "hp:t가 생성돼 있어야 한다"
        run = parent_map[node.t_elems[0]]
        assert tag(run) == "run"
        assert run.get("charPrIDRef"), "charPrIDRef가 복제돼야 한다"

    def test_empty_new_text_on_empty_node_is_skipped(self, empty_cell_hwpx, tmp_path):
        """빈 노드에 빈 텍스트 → 실질 동일이므로 skip (불필요한 삽입 없음)."""
        before = _global_nodes(empty_cell_hwpx, tmp_path / "before")
        gid = self._empty_cell_gids(before)[0]
        out = tmp_path / "noop.hwpx"
        result = apply_edits(empty_cell_hwpx, [{"id": gid, "new_text": "  "}], out)
        assert result.applied_ids == []
        assert result.skipped_ids == [gid]
        after = _global_nodes(out, tmp_path / "after")
        assert not after[gid].t_elems  # 여전히 hp:t 없음
