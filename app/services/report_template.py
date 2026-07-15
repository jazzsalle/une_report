"""표준 템플릿(서식 표본 hwpx) 기반 보고서 조립.

`templates/` 의 hwpx는 **서식 표본 문서**다 (규약 — 새 템플릿도 이 순서를 지킬 것):

  p0 = 문서 제목 표본 (가운데 정렬 대자)
  p1 = 부제 표본 ("서면 보고 / 날짜 / 작성자")
  이후 처음 나오는:
    "1."  로 시작하는 문단 → 헤딩1 표본
    "가." 로 시작하는 문단 → 헤딩2 표본
    "○"  로 시작하는 문단 → 개조식1 표본 (들여쓰기는 텍스트 앞 공백)
    "-"  로 시작하는 문단 → 개조식2 표본
    hp:tbl                → 표 표본 (없어도 됨 — 기본 표 생성으로 폴백)

조립은 템플릿 패키지를 통째 복사한 뒤 section0 본문만 표본 복제 문단으로
재구성한다 — header.xml의 charPr/paraPr/borderFill 참조가 그대로 유효해
글꼴·크기·정렬·표 테두리 서식이 보존된다. 텍스트는 첫 hp:t에 기록하고
편집 문단의 linesegarray를 제거한다 (한컴 줄겹침 방지 — edits.py 노하우).
"""
import copy
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from app import config
from app.core.hwpx.models import HWP_UNITS_PER_MM
from app.core.hwpx.package import extract_hwpx, find_section_files, repack_hwpx
from app.core.hwpx.edits import _strip_lineseg_cache
from app.core.hwpx.xml_utils import register_namespaces, t_full_text, tag

_HEADING1_RE = re.compile(r"^\d+\.")
_HEADING2_RE = re.compile(r"^[가-하]\.")


@dataclass
class Exemplar:
    """서식 표본 문단 — 복제 원형과 들여쓰기 접두."""

    elem: ET.Element  # hp:p (원본 트리 내 참조 — 복제해서 쓴다)
    indent: str       # 원문 텍스트의 leading 공백 (들여쓰기 표현)


class TemplateError(ValueError):
    """템플릿 규약 위반 (표본 문단 누락 등)."""


def list_templates() -> list[dict]:
    """templates/ 의 표준 템플릿 목록 — [{"id", "name", "has_table"}]."""
    out: list[dict] = []
    tdir = Path(config.REPORT_TEMPLATES_DIR)
    if not tdir.is_dir():
        return out
    for path in sorted(tdir.glob("*.hwpx")):
        has_table = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                extract_hwpx(path, Path(tmp) / "x")
                sf = find_section_files(Path(tmp) / "x")[0]
                has_table = "tbl" in {tag(e) for e in ET.parse(sf).getroot().iter()}
        except Exception:
            continue  # 손상 파일은 목록에서 제외
        out.append({"id": path.stem, "name": path.stem, "has_table": has_table})
    return out


def template_path(template_id: str) -> Path:
    path = Path(config.REPORT_TEMPLATES_DIR) / f"{template_id}.hwpx"
    if not path.is_file():
        raise TemplateError(f"템플릿을 찾을 수 없습니다: {template_id}")
    return path


def _p_text(p: ET.Element) -> str:
    """문단의 전체 텍스트 (fwSpace tail 포함 — t_full_text)."""
    parts: list[str] = []
    for run in p:
        if tag(run) != "run":
            continue
        for child in run:
            if tag(child) == "t":
                parts.append(t_full_text(child))
    return "".join(parts)


def _indent_of(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def _find_exemplars(root: ET.Element) -> dict:
    """섹션에서 규약 순서대로 표본을 인식한다. 누락 시 TemplateError."""
    tops = [p for p in root if tag(p) == "p"]
    if len(tops) < 2:
        raise TemplateError("템플릿에 제목·부제 표본 문단(p0·p1)이 없습니다")

    found: dict = {
        "title": Exemplar(tops[0], ""),
        "subtitle": Exemplar(tops[1], ""),
        "blank": None,      # 빈 줄 표본 (없으면 부제 복제로 대체)
        "heading1": None,
        "heading2": None,
        "bullet1": None,
        "bullet2": None,
        "table_host": None,  # 표를 담은 hp:p (tbl 포함)
    }
    for p in tops[2:]:
        raw = _p_text(p)
        stripped = raw.strip()
        if found["table_host"] is None and any(
            tag(c) == "tbl" for run in p if tag(run) == "run" for c in run
        ):
            found["table_host"] = Exemplar(p, "")
            continue
        if not stripped:
            if found["blank"] is None:
                found["blank"] = Exemplar(p, "")
            continue
        if found["heading1"] is None and _HEADING1_RE.match(stripped):
            found["heading1"] = Exemplar(p, _indent_of(raw))
        elif found["heading2"] is None and _HEADING2_RE.match(stripped):
            found["heading2"] = Exemplar(p, _indent_of(raw))
        elif found["bullet1"] is None and stripped.startswith("○"):
            found["bullet1"] = Exemplar(p, _indent_of(raw))
        elif found["bullet2"] is None and stripped.startswith("-"):
            found["bullet2"] = Exemplar(p, _indent_of(raw))

    missing = [k for k in ("heading1", "heading2", "bullet1", "bullet2") if found[k] is None]
    if missing:
        raise TemplateError(f"템플릿 표본 문단이 없습니다: {', '.join(missing)} (규약: 모듈 docstring)")
    if found["blank"] is None:
        blank = copy.deepcopy(found["subtitle"].elem)
        _set_p_text(blank, "")
        found["blank"] = Exemplar(blank, "")
    return found


def _set_p_text(p: ET.Element, text: str) -> None:
    """문단의 첫 hp:t에 text를 기록하고 나머지 t는 비운다 + lineseg 제거.

    hp:t가 없는 문단(한컴 빈 셀은 run만 있고 t가 없다 — A1 실측)은
    첫 run에 t를 생성 삽입한다. run조차 없으면 run+t를 만든다.
    """
    ns = _hp_ns(p)
    written = False
    first_run = None
    for run in p:
        if tag(run) != "run":
            continue
        if first_run is None:
            first_run = run
        for child in run:
            if tag(child) == "t":
                for sub in list(child):
                    child.remove(sub)  # fwSpace 등 잔존 텍스트 제거
                child.text = "" if written else text
                written = True
    if not written:
        if first_run is None:
            first_run = ET.SubElement(p, f"{ns}run")
        t = ET.SubElement(first_run, f"{ns}t")
        t.text = text
    _strip_lineseg_cache(p)


def _clone_with_text(ex: Exemplar, text: str) -> ET.Element:
    p = copy.deepcopy(ex.elem)
    _set_p_text(p, (ex.indent + text.strip()) if text.strip() else "")
    return p


def _hp_ns(elem: ET.Element) -> str:
    return elem.tag.split("}")[0] + "}" if elem.tag.startswith("{") else ""


def _resize_table(host: Exemplar, rows_data: list[list[str]], table_width_mm: int) -> ET.Element:
    """표 표본을 rows_data 크기로 리사이즈한 호스트 문단(hp:p)을 만든다.

    첫 셀을 원형으로 전 셀을 복제하므로 표본의 테두리(borderFill)·셀 서식이
    전 셀에 적용된다. 열폭은 table_width_mm 균등 분배 (기존 규칙 유지).
    """
    host_p = copy.deepcopy(host.elem)
    _strip_lineseg_cache(host_p)
    tbl = next(c for run in host_p if tag(run) == "run" for c in run if tag(c) == "tbl")
    ns = _hp_ns(tbl)

    trs = [c for c in tbl if tag(c) == "tr"]
    proto_tc = next(c for c in trs[0] if tag(c) == "tc")
    proto_cell_h = 1000
    for e in proto_tc:
        if tag(e) == "cellSz":
            try:
                proto_cell_h = int(e.get("height") or proto_cell_h)
            except ValueError:
                pass
    for tr in trs:
        tbl.remove(tr)

    n_rows = len(rows_data)
    n_cols = max(len(r) for r in rows_data)
    col_w = round(table_width_mm * HWP_UNITS_PER_MM / n_cols)
    tbl.set("rowCnt", str(n_rows))
    tbl.set("colCnt", str(n_cols))
    for e in tbl:
        if tag(e) == "sz":
            e.set("width", str(col_w * n_cols))
            e.set("height", str(proto_cell_h * n_rows))

    for r, row in enumerate(rows_data):
        tr = ET.SubElement(tbl, f"{ns}tr")
        for c in range(n_cols):
            tc = copy.deepcopy(proto_tc)
            for e in tc:
                etag = tag(e)
                if etag == "cellAddr":
                    e.set("colAddr", str(c))
                    e.set("rowAddr", str(r))
                elif etag == "cellSpan":
                    e.set("colSpan", "1")
                    e.set("rowSpan", "1")
                elif etag == "cellSz":
                    e.set("width", str(col_w))
                elif etag == "subList":
                    for p in e:
                        if tag(p) == "p":
                            _set_p_text(p, str(row[c]) if c < len(row) else "")
            tr.append(tc)
    return host_p


def assemble_hwpx(
    template_id: str,
    title: str,
    subtitle: str,
    sections: list[dict],
    output_path: str | Path,
    *,
    table_width_mm: int,
) -> None:
    """템플릿 서식으로 보고서 hwpx를 조립한다 (모듈 docstring 참조).

    sections는 report API의 {"name","content","references","children"} 트리.
    p0(제목)·p1(부제)은 제자리 텍스트 교체(첫 문단 run의 secPr 등 구조 보존),
    이후 본문은 표본 복제 문단으로 재구성한다.
    """
    from app.services.report_builder import _references_line, markdown_blocks

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "tpl"
        compress_info, file_order = extract_hwpx(template_path(template_id), extract_dir)
        sf = find_section_files(extract_dir)[0]
        register_namespaces(sf)
        tree = ET.parse(sf)
        root = tree.getroot()
        ex = _find_exemplars(root)

        # 표본 원형은 재구성 전에 복제해 확보 (원본 트리는 곧 비워진다)
        ex = {
            k: (Exemplar(copy.deepcopy(v.elem), v.indent) if isinstance(v, Exemplar) else v)
            for k, v in ex.items()
        }

        # p0·p1 제자리 교체 (첫 문단 run의 secPr 등 섹션 속성 보존), 나머지 제거
        tops = [p for p in root if tag(p) == "p"]
        _set_p_text(tops[0], title.strip())
        _set_p_text(tops[1], subtitle.strip())
        for p in tops[2:]:
            root.remove(p)

        def _append(elem: ET.Element) -> None:
            root.append(elem)

        def _emit_paragraph(text: str) -> None:
            stripped = text.strip()
            if stripped.startswith("○"):
                _append(_clone_with_text(ex["bullet1"], stripped))
            elif stripped.startswith(("-", "–", "—", "―", "ㆍ", "·")):
                _append(_clone_with_text(ex["bullet2"], stripped))
            else:
                # 마커 없는 서술 문단·□ 등은 개조식1 서식(들여쓰기 없이)
                p = copy.deepcopy(ex["bullet1"].elem)
                _set_p_text(p, stripped)
                _append(p)

        def _emit_table(rows: list[list[str]]) -> None:
            if not rows or max(len(r) for r in rows) == 0:
                return
            if ex["table_host"] is not None:
                _append(_resize_table(ex["table_host"], rows, table_width_mm))
            else:
                # 표 표본이 없는 템플릿 — 기본 표 생성 폴백은 호출부(report_builder)가
                # 담당하기 어려우므로 텍스트 행으로 보존한다
                for row in rows:
                    _emit_paragraph(" | ".join(str(v) for v in row))

        def _walk(nodes: list[dict], depth: int = 0) -> None:
            for node in nodes or []:
                name = str(node.get("name") or "").strip()
                if name:
                    key = "heading1" if depth == 0 else "heading2"
                    _append(_clone_with_text(ex[key], name))
                for kind, payload in markdown_blocks(str(node.get("content") or "")):
                    if kind == "p":
                        _emit_paragraph(payload)
                    else:
                        _emit_table(payload)
                ref_line = _references_line(node.get("references") or [])
                if ref_line:
                    _append(_clone_with_text(ex["bullet2"], ref_line))
                _walk(node.get("children") or [], depth + 1)

        blank_p = copy.deepcopy(ex["blank"].elem)
        _strip_lineseg_cache(blank_p)
        _append(blank_p)
        _walk(sections)

        tree.write(sf, xml_declaration=True, encoding="utf-8")
        repack_hwpx(extract_dir, output_path, compress_info, file_order)


def load_template_styles(template_id: str) -> dict:
    """docx 근사용 스타일 추출 — {역할: {"size_pt","bold","center","indent_mm"}}.

    역할별 charPr(height·bold)와 제목 paraPr 정렬을 header.xml에서 읽는다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "tpl"
        extract_hwpx(template_path(template_id), extract_dir)
        sf = find_section_files(extract_dir)[0]
        root = ET.parse(sf).getroot()
        ex = _find_exemplars(root)

        header = ET.parse(extract_dir / "Contents" / "header.xml").getroot()
        char_prs = {e.get("id"): e for e in header.iter() if tag(e) == "charPr"}
        para_prs = {e.get("id"): e for e in header.iter() if tag(e) == "paraPr"}

        def _first_char_ref(p: ET.Element) -> str | None:
            for run in p:
                if tag(run) == "run" and run.get("charPrIDRef"):
                    return run.get("charPrIDRef")
            return None

        def _style(exemplar: Exemplar) -> dict:
            cp = char_prs.get(_first_char_ref(exemplar.elem) or "")
            size_pt = 10.0
            bold = False
            if cp is not None:
                try:
                    size_pt = int(cp.get("height") or 1000) / 100
                except ValueError:
                    pass
                bold = any(tag(c) == "bold" for c in cp)
            pp = para_prs.get(exemplar.elem.get("paraPrIDRef") or "")
            center = False
            if pp is not None:
                align = next((c for c in pp if tag(c) == "align"), None)
                center = align is not None and align.get("horizontal") == "CENTER"
            return {
                "size_pt": size_pt,
                "bold": bold,
                "center": center,
                "indent_mm": len(exemplar.indent) * 2,  # 공백 1칸 ≈ 2mm 근사
            }

        styles = {
            "title": _style(ex["title"]),
            "subtitle": _style(ex["subtitle"]),
            "heading1": _style(ex["heading1"]),
            "heading2": _style(ex["heading2"]),
            "bullet1": _style(ex["bullet1"]),
            "bullet2": _style(ex["bullet2"]),
        }
        if ex["table_host"] is not None:
            # 표 셀 서식: 첫 셀 문단의 charPr
            tbl_p = ex["table_host"].elem
            tc_p = next(
                (p for p in tbl_p.iter() if tag(p) == "p" and p is not tbl_p), None
            )
            if tc_p is not None:
                styles["cell"] = _style(Exemplar(tc_p, ""))
        return styles
