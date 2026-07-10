"""parser.py·html.py 테스트.

핵심 불변식: hwpx_to_html이 주입하는 data-id 집합은
parse_section의 전역 id 집합(섹션 로컬 id + 이전 섹션 노드 수 누적)과
정확히 일치해야 한다. 이 불변식이 깨지면 편집 UI의 선택 대상과
apply_edits의 편집 대상이 어긋난다.
"""
import re
from pathlib import Path

import pytest

from app.core.hwpx import extract_hwpx, find_section_files, hwpx_to_html, parse_section

_DATA_ID_RE = re.compile(r'data-id="(\d+)"')


def _parse_all_sections(hwpx_path: Path, tmp_path: Path) -> list:
    """모든 섹션을 파싱해 전역 id가 부여된 TextNode 목록을 반환한다."""
    extract_dir = tmp_path / f"extract_{hwpx_path.stem}"
    extract_hwpx(hwpx_path, extract_dir)
    section_files = find_section_files(extract_dir)
    assert section_files, "섹션 파일이 최소 1개는 있어야 함"

    all_nodes = []
    offset = 0
    for sf in section_files:
        nodes, _tree, _parent_map, _t_ns = parse_section(sf)
        for n in nodes:
            all_nodes.append((offset + n.id, n))
        offset += len(nodes)
    return all_nodes


class TestParseSection:
    def test_demo_form_node_structure(self, demo_form_hwpx, tmp_path):
        """builder 생성 문서의 노드 수·타입·순서가 spec과 일치해야 한다."""
        nodes = [n for _gid, n in _parse_all_sections(demo_form_hwpx, tmp_path)]
        texts = [n.text for n in nodes]

        # spec의 제목·문단·표 내용이 모두 노드로 추출됨
        assert "재난 대응 계획서 (테스트)" in texts
        assert "본 문서는 단위 테스트용 임시 양식이다." in texts
        assert "수립 주기" in texts  # 표 0 셀
        assert "[주소 입력]" in texts  # placeholders_demo 표 셀

        # 로컬 id는 0부터 빈틈없는 순번
        assert [n.id for n in nodes] == list(range(len(nodes)))

    def test_demo_form_table_cell_coordinates(self, demo_form_hwpx, tmp_path):
        """표 셀의 table_idx/row/col/span이 spec의 격자 구조와 일치해야 한다."""
        nodes = [n for _gid, n in _parse_all_sections(demo_form_hwpx, tmp_path)]
        cells = [n for n in nodes if n.type == "table_cell"]
        assert cells, "표 셀 노드가 있어야 함"

        # 표 0: spec의 2x2 표, 표 1: placeholders_demo의 3x2 표
        grid0 = {(c.row, c.col) for c in cells if c.table_idx == 0}
        grid1 = {(c.row, c.col) for c in cells if c.table_idx == 1}
        assert grid0 == {(0, 0), (0, 1), (1, 0), (1, 1)}
        assert grid1 == {(r, c) for r in range(3) for c in range(2)}

        # 병합 없는 표이므로 span은 모두 1, 크기 정보는 양수로 환산됨
        for c in cells:
            assert c.cell_col_span == 1 and c.cell_row_span == 1
            assert c.cell_width_mm > 0

        # 셀 내용 대조 (표 1의 (2,1) = "[주소 입력]")
        by_pos = {(c.table_idx, c.row, c.col): c.text for c in cells}
        assert by_pos[(0, 0, 0)] == "구분"
        assert by_pos[(0, 1, 1)] == "연 1회"
        assert by_pos[(1, 2, 1)] == "[주소 입력]"

    def test_report_table_nodes(self, report_table_hwpx, tmp_path):
        """실제 도구로 만든 report_table.hwpx에서 문단·표 셀이 추출돼야 한다."""
        nodes = [n for _gid, n in _parse_all_sections(report_table_hwpx, tmp_path)]
        body = [n for n in nodes if n.type == "body_text"]
        cells = [n for n in nodes if n.type == "table_cell"]
        assert body and cells

        # 셀 좌표는 (row, col) 격자를 이루고 중복이 없어야 함
        coords = [(c.table_idx, c.row, c.col) for c in cells]
        assert len(coords) == len(set(coords))
        for c in cells:
            assert c.row >= 0 and c.col >= 0
            assert c.cell_col_span >= 1 and c.cell_row_span >= 1

    def test_body_text_has_no_table_coords(self, demo_form_hwpx, tmp_path):
        nodes = [n for _gid, n in _parse_all_sections(demo_form_hwpx, tmp_path)]
        for n in nodes:
            if n.type == "body_text":
                assert (n.table_idx, n.row, n.col) == (-1, -1, -1)


class TestHtmlInvariant:
    @pytest.mark.parametrize("fixture_name", ["report_table_hwpx", "demo_form_hwpx"])
    def test_data_id_set_equals_global_parse_ids(self, fixture_name, request, tmp_path):
        """[핵심 불변식] HTML data-id 집합 == parse_section 전역 id 집합."""
        hwpx_path = request.getfixturevalue(fixture_name)
        result = hwpx_to_html(hwpx_path, inject_ids=True)

        html_ids = [int(m) for m in _DATA_ID_RE.findall(result.html)]
        assert len(html_ids) == len(set(html_ids)), "data-id 중복 금지"

        global_ids = {gid for gid, _n in _parse_all_sections(hwpx_path, tmp_path)}
        assert set(html_ids) == global_ids
        assert result.node_count == len(global_ids)

    def test_node_texts_present_in_html(self, demo_form_hwpx):
        """노드 텍스트(placeholder 포함)가 HTML 본문에 렌더돼야 한다."""
        html = hwpx_to_html(demo_form_hwpx).html
        assert "재난 대응 계획서 (테스트)" in html
        assert "수립 기관: [기관명]" in html
        assert "&lt;담당자 이름&gt;" in html  # 꺾쇠는 escape되어 출력
        assert "[주소 입력]" in html


class TestHtmlOptions:
    def test_inject_ids_false(self, report_table_hwpx):
        """inject_ids=False면 data-id가 전혀 없고 .page div는 존재해야 한다."""
        result = hwpx_to_html(report_table_hwpx, inject_ids=False)
        assert "data-id" not in result.html
        assert 'class="page"' in result.html
        assert result.page_count >= 1
        assert result.node_count > 0  # 노드 수 집계는 id 주입과 무관

    def test_split_pages_false_single_page(self, report_table_hwpx):
        result = hwpx_to_html(report_table_hwpx, split_pages=False)
        assert result.page_count == 1
        assert result.html.count('class="page"') == 1

    def test_split_pages_true_page_per_section(self, demo_form_hwpx, tmp_path):
        """split_pages=True면 .page div 수 == 섹션 수."""
        extract_dir = tmp_path / "x"
        extract_hwpx(demo_form_hwpx, extract_dir)
        n_sections = len(find_section_files(extract_dir))
        result = hwpx_to_html(demo_form_hwpx, split_pages=True)
        assert result.page_count == n_sections
        assert result.html.count('class="page"') == n_sections

    def test_html_is_self_contained_document(self, demo_form_hwpx):
        html = hwpx_to_html(demo_form_hwpx).html
        assert html.lstrip().lower().startswith("<!doctype html>")
        assert "<style>" in html  # 인라인 CSS 포함(외부 리소스 없음)
