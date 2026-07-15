"""표준 템플릿 조립(report_template) 테스트 — templates/ 실물 사용, 네트워크 0회."""
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app import config
from app.core.hwpx import extract_hwpx, find_section_files, parse_section, validate_hwpx
from app.core.hwpx.xml_utils import tag
from app.services.report_builder import TABLE_WIDTH_MM, build_report_docx, build_report_hwpx
from app.services.report_template import (
    TemplateError,
    _find_exemplars,
    list_templates,
    load_template_styles,
    template_path,
)

TPL1 = "AI 행정문서 템플릿"
TPL2 = "AI 행정문서 템플릿2"

SECTIONS = [
    {"name": "1. 추진 배경", "content": "", "references": [], "children": [
        {"name": "가. 계획 수립 근거", "children": [],
         "content": "○ 신종 변이 확산으로 재유행 우려가 커지고 있다.\n- 세부 근거는 다음과 같다.",
         "references": [{"id": "c", "fileId": "f", "fileName": "지침.pdf", "page": "2"}]},
    ]},
    {"name": "2. 추진 내용", "children": [], "references": [],
     "content": "발생 현황 표는 다음과 같다.\n| 구분 | 건수 | 비고 |\n| 1분기 | 100 | - |"},
]


def _template_available() -> bool:
    return Path(config.REPORT_TEMPLATES_DIR, f"{TPL1}.hwpx").is_file()


pytestmark = pytest.mark.skipif(
    not _template_available(), reason="templates/ 표준 템플릿 미설치"
)


def _parse_out(out: Path, tmp_path: Path):
    extract_dir = tmp_path / f"x_{out.stem}"
    extract_hwpx(out, extract_dir)
    return parse_section(find_section_files(extract_dir)[0])


class TestListAndRecognize:
    def test_list_templates_reports_table_capability(self):
        items = {t["id"]: t for t in list_templates()}
        assert TPL1 in items and TPL2 in items
        assert items[TPL1]["has_table"] is True
        assert items[TPL2]["has_table"] is False

    @pytest.mark.parametrize("tpl", [TPL1, TPL2])
    def test_exemplars_recognized(self, tpl, tmp_path):
        extract_dir = tmp_path / "t"
        extract_hwpx(template_path(tpl), extract_dir)
        root = ET.parse(find_section_files(extract_dir)[0]).getroot()
        ex = _find_exemplars(root)
        for key in ("title", "subtitle", "heading1", "heading2", "bullet1", "bullet2"):
            assert ex[key] is not None, key
        assert (ex["table_host"] is not None) == (tpl == TPL1)
        # 개조식 들여쓰기 접두가 보존된다
        assert ex["bullet1"].indent and ex["bullet2"].indent
        assert len(ex["bullet2"].indent) > len(ex["bullet1"].indent)


class TestAssembleHwpx:
    def test_roundtrip_with_template_styles(self, tmp_path):
        out = tmp_path / "styled.hwpx"
        build_report_hwpx(
            "코로나19 재유행 대비계획", SECTIONS, out,
            subtitle="서면 보고 / 2026. 7. 15.(수) / 재난안전계획 수립 담당자",
            template_id=TPL1,
        )
        assert validate_hwpx(out).ok
        nodes, tree, _pm, _ns = _parse_out(out, tmp_path)
        texts = [n.text for n in nodes]

        assert texts[0] == "코로나19 재유행 대비계획"          # p0 제자리 교체
        assert texts[1].startswith("서면 보고 /")              # p1 부제
        assert "1. 추진 배경" in texts
        assert "가. 계획 수립 근거" in texts
        assert any(t.startswith("○") for t in texts)
        assert any(t.startswith("※ 참고:") for t in texts)
        assert "100" in texts                                   # 표 셀

        # 서식 매핑: 표본 charPr가 그대로 쓰였다 (제목=10, 헤딩1=11, 개조식=7)
        root = tree.getroot()
        tops = [p for p in root if tag(p) == "p"]
        def char_ref(p):
            return next((r.get("charPrIDRef") for r in p if tag(r) == "run"), None)
        by_text = {}
        for p in tops:
            joined = "".join(
                t.text or "" for r in p if tag(r) == "run" for t in r if tag(t) == "t"
            ).strip()
            by_text.setdefault(joined, p)
        assert char_ref(by_text["코로나19 재유행 대비계획"]) == "10"
        assert char_ref(by_text["1. 추진 배경"]) == "11"
        assert char_ref(by_text["가. 계획 수립 근거"]) == "8"

        # 편집 문단에 stale lineseg가 없어야 한다 (줄겹침 방지)
        for p in tops:
            assert not any(tag(c) == "linesegarray" for c in p), "lineseg 잔존"

    def test_table_resized_with_template_borders_and_160mm(self, tmp_path):
        out = tmp_path / "tbl.hwpx"
        build_report_hwpx("표 테스트", SECTIONS, out, template_id=TPL1)
        nodes, *_ = _parse_out(out, tmp_path)
        cells = [n for n in nodes if n.type == "table_cell"]
        rows = {c.row for c in cells}
        cols = {c.col for c in cells}
        assert rows == {0, 1} and cols == {0, 1, 2}  # 2×3으로 리사이즈
        head = [c for c in cells if c.row == 0]
        widths = [c.cell_width_mm for c in head]
        assert all(w == widths[0] for w in widths)
        assert abs(sum(widths) - TABLE_WIDTH_MM) <= 2

    def test_template2_without_table_exemplar_falls_back_to_text(self, tmp_path):
        """표 표본이 없는 템플릿2는 표를 텍스트 행으로 보존한다 (데이터 유실 없음)."""
        out = tmp_path / "tpl2.hwpx"
        build_report_hwpx("표 없는 템플릿", SECTIONS, out, template_id=TPL2)
        assert validate_hwpx(out).ok
        nodes, *_ = _parse_out(out, tmp_path)
        texts = [n.text for n in nodes]
        assert not any(n.type == "table_cell" for n in nodes)
        assert any("1분기" in t and "100" in t for t in texts)  # 표 내용 보존

    def test_unknown_template_raises(self, tmp_path):
        with pytest.raises(TemplateError):
            build_report_hwpx("제목", SECTIONS, tmp_path / "x.hwpx", template_id="없는템플릿")


class TestDocxApproximation:
    def test_styles_extracted(self):
        styles = load_template_styles(TPL1)
        assert styles["title"]["size_pt"] == 20.0
        assert styles["title"]["center"] is True
        assert styles["heading1"]["size_pt"] == 15.0
        assert styles["heading1"]["bold"] is True
        assert styles["bullet1"]["size_pt"] == 13.0
        assert styles["bullet2"]["indent_mm"] > styles["bullet1"]["indent_mm"]
        assert styles["cell"]["size_pt"] == 11.0

    def test_docx_applies_approximation(self, tmp_path):
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        out = tmp_path / "styled.docx"
        build_report_docx(
            "코로나19 재유행 대비계획", SECTIONS, out,
            subtitle="서면 보고 / 2026. 7. 15.(수) / 담당자",
            template_styles=load_template_styles(TPL1),
        )
        doc = Document(str(out))
        by_text = {p.text: p for p in doc.paragraphs if p.text}
        title_p = by_text["코로나19 재유행 대비계획"]
        assert title_p.alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert title_p.runs[0].font.size == Pt(20)
        h1 = by_text["1. 추진 배경"]
        assert h1.runs[0].bold is True
        assert h1.runs[0].font.size == Pt(15)
        bullet = next(p for t, p in by_text.items() if t.startswith("○"))
        assert bullet.paragraph_format.left_indent is not None
