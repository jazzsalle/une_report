"""hwpx → 페이지 분할 HTML 변환기 (M1-3).

hwpx(OWPML)를 self-contained HTML 문자열로 변환한다. 편집 UI가 문단·표 셀을
식별할 수 있도록 문단은 <p data-id="N">, 표 셀은 <td data-id="N">으로 렌더한다.

data-id 순번 규칙 — parser.parse_section의 노드 순번 규칙(해당 모듈 docstring)과
반드시 일치해야 한다(핵심 불변식). 이를 보장하기 위해 id를 자체 재부여하지 않고,
parse_section이 반환한 노드 목록을 **동일한 순회 순서로 소비**하며 XML 요소
(hp:p, hp:tc)에 매핑한다(_map_node_ids). 순회 순서가 어긋나면 노드 type
불일치로 즉시 예외가 발생한다. 전역 id = 섹션 로컬 id + 이전 섹션들의 노드 수 합.

렌더링 범위:
- 문단 텍스트(run별 charPr 서식), 표(cellSpan의 colspan/rowspan 반영),
  이미지(BinData → base64 data URI 인라인)
- header.xml의 charPr/paraPr/borderFill → CSS 클래스(.chN/.paN/.bfN) 매핑
- split_pages=True면 섹션 단위로 .page div 분할
  (lineseg 좌표 기반 정밀 페이지네이션은 범위 외 — 섹션에 실제 페이지 여러 장이
  담길 수 있으므로 .page는 고정 height 대신 min-height를 쓴다)
- 수식·도형·각주 등 미지원 객체는 렌더 생략

변환은 읽기 전용이다: 원본 hwpx는 임시 디렉터리에 해제해서만 읽고,
파싱한 XML 트리도 수정하지 않는다.

구조·알고리즘은 process-gpt-office-mcp의 hwpx_to_html.py를 참고해
자체 재작성했다(코드 복사 없음). 참고 구현과 달리 색상 BGR 스왑은 하지 않는다
— OWPML(KS X 6101) 색상 표기는 "#RRGGBB"로 판단(스펙 기반 추정).
"""
import base64
import re
import tempfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from .package import extract_hwpx, find_section_files
# 노드 순번 규칙의 단일 소스를 유지하기 위해 parser의 내부 헬퍼를 공유한다.
from .parser import _find_toplevel_tables, collect_runs_and_texts, parse_section
from .xml_utils import tag

__all__ = ["HtmlResult", "hwpx_to_html"]

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z_-]")


@dataclass
class HtmlResult:
    """hwpx_to_html 변환 결과.

    - html: self-contained HTML 문서 문자열(인라인 <style> 포함)
    - page_count: 출력된 .page div 수 (split_pages=True면 섹션 수)
    - node_count: 전체 섹션의 TextNode 수(= 전역 data-id 개수 상한)
    """

    html: str
    page_count: int
    node_count: int


# ---------------------------------------------------------------------------
# 단위·색상 헬퍼
# ---------------------------------------------------------------------------

def _hwpunit_to_px(value: str | int | None) -> str | None:
    """HWPUNIT(1/7200 inch)을 96dpi 기준 px 문자열로 변환한다. 0 이하·비정상 값은 None."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0 or num >= 4294967295:  # 상한 근처 값은 '지정 안 함' 센티널
        return None
    return f"{num / 7200 * 96:.2f}px"


def _mm_to_px(value: str | None) -> str | None:
    """"0.12 mm" 같은 mm 문자열을 px 문자열로 변환한다."""
    if not value:
        return None
    try:
        num = float(value.replace("mm", "").strip())
    except ValueError:
        return None
    if num <= 0:
        return None
    return f"{num / 25.4 * 96:.2f}px"


def _color(value: str | None) -> str | None:
    """색상 값 정규화. "none"은 None, "#AARRGGBB"는 알파를 제거해 "#RRGGBB"로."""
    if not value or value.lower() == "none":
        return None
    if len(value) == 9 and value.startswith("#"):
        return "#" + value[-6:]
    return value


def _safe_id(value: str | None) -> str:
    """CSS 클래스명에 쓸 수 있게 id 문자열을 정제한다(영숫자·-·_만 허용)."""
    return _SAFE_ID_RE.sub("", value or "")


def _first(elem: ET.Element, name: str) -> ET.Element | None:
    """직계 자식 중 로컬 태그명이 name인 첫 요소를 반환한다."""
    for child in elem:
        if tag(child) == name:
            return child
    return None


def _build_decl(style_map: dict[str, str]) -> str:
    """{속성: 값} dict를 CSS 선언 문자열로 만든다."""
    return "; ".join(f"{k}: {v}" for k, v in style_map.items() if v)


# ---------------------------------------------------------------------------
# header.xml → CSS 클래스 매핑
# ---------------------------------------------------------------------------

@dataclass
class _HeaderStyles:
    """header.xml에서 해석한 스타일. 각 dict는 {id: CSS 선언}(비어있지 않은 것만)."""

    char_css: dict[str, str] = field(default_factory=dict)
    para_css: dict[str, str] = field(default_factory=dict)
    border_css: dict[str, str] = field(default_factory=dict)
    # hh:style id → (paraPrIDRef, charPrIDRef). 문단 styleIDRef 해석용.
    style_refs: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


def _edge_css(edge: ET.Element | None) -> str:
    """borderFill의 한 변(leftBorder 등)을 CSS border 값으로 변환한다."""
    if edge is None:
        return "none"
    etype = (edge.get("type") or "NONE").upper()
    if etype == "NONE":
        return "none"
    width = _mm_to_px(edge.get("width")) or "1px"
    if etype.startswith("DASH"):
        style = "dashed"
    elif etype.startswith("DOT"):
        style = "dotted"
    else:
        style = "solid"
    color = _color(edge.get("color")) or "#000"
    return f"{width} {style} {color}"


def _parse_header(header_path: Path) -> _HeaderStyles:
    """Contents/header.xml에서 charPr·paraPr·borderFill·style을 해석한다.

    header가 없거나 파스에 실패해도 예외 없이 빈 스타일을 반환한다
    (스타일 없는 평문 렌더로 강등).
    """
    hs = _HeaderStyles()
    if not header_path.is_file():
        return hs
    try:
        root = ET.parse(str(header_path)).getroot()
    except ET.ParseError:
        return hs

    # 글꼴: (lang, font id) → face 이름
    fontfaces: dict[tuple[str | None, str | None], str | None] = {}
    for ff in root.iter():
        if tag(ff) != "fontface":
            continue
        lang = ff.get("lang")
        for font in ff:
            if tag(font) == "font":
                fontfaces[(lang, font.get("id"))] = font.get("face")

    # borderFill → 4변 border + 배경색
    for bf in root.iter():
        if tag(bf) != "borderFill":
            continue
        bid = _safe_id(bf.get("id"))
        if not bid:
            continue
        decl: dict[str, str] = {}
        for side, edge_name in (
            ("left", "leftBorder"), ("right", "rightBorder"),
            ("top", "topBorder"), ("bottom", "bottomBorder"),
        ):
            decl[f"border-{side}"] = _edge_css(_first(bf, edge_name))
        win = next((e for e in bf.iter() if tag(e) == "winBrush"), None)
        if win is not None:
            fill = _color(win.get("faceColor"))
            if fill and fill.lower() != "#ffffff":
                decl["background-color"] = fill
        hs.border_css[bid] = _build_decl(decl)

    # charPr → 글자 서식
    for cp in root.iter():
        if tag(cp) != "charPr":
            continue
        cid = _safe_id(cp.get("id"))
        if not cid:
            continue
        decl = {}
        height = cp.get("height")
        if height and height.isdigit() and int(height) > 0:
            decl["font-size"] = f"{int(height) / 100:g}pt"
        color = _color(cp.get("textColor"))
        if color:
            decl["color"] = color
        shade = _color(cp.get("shadeColor"))
        if shade and shade.lower() != "#ffffff":
            decl["background-color"] = shade
        font_ref = _first(cp, "fontRef")
        if font_ref is not None:
            fid = font_ref.get("hangul")
            face = fontfaces.get(("HANGUL", fid)) or fontfaces.get(("LATIN", fid))
            if face:
                decl["font-family"] = "'" + face.replace("'", "\\'") + "'"
        deco: list[str] = []
        for child in cp:
            ctag = tag(child)
            if ctag == "bold":
                decl["font-weight"] = "700"
            elif ctag == "italic":
                decl["font-style"] = "italic"
            elif ctag == "underline":
                # 속성이 없으면 요소 존재 자체를 의도로 본다. type="NONE"만 제외.
                if (child.get("type") or "BOTTOM").upper() != "NONE":
                    deco.append("underline")
            elif ctag == "strikeout":
                if (child.get("shape") or child.get("type") or "SOLID").upper() != "NONE":
                    deco.append("line-through")
        if deco:
            decl["text-decoration-line"] = " ".join(deco)
        if decl:
            hs.char_css[cid] = _build_decl(decl)

    # paraPr → 문단 서식
    for pp in root.iter():
        if tag(pp) != "paraPr":
            continue
        pid = _safe_id(pp.get("id"))
        if not pid:
            continue
        decl = {}
        align = _first(pp, "align")
        if align is not None:
            horiz = (align.get("horizontal") or "").upper()
            if horiz == "CENTER":
                decl["text-align"] = "center"
            elif horiz == "RIGHT":
                decl["text-align"] = "right"
            elif horiz in ("JUSTIFY", "DISTRIBUTE", "BOTH"):
                decl["text-align"] = "justify"
            # LEFT는 기본값이므로 생략
        ls = _first(pp, "lineSpacing")
        if ls is not None:
            ls_type = (ls.get("type") or "").upper()
            val = ls.get("value")
            if ls_type == "PERCENT" and val and val.isdigit():
                decl["line-height"] = f"{int(val)}%"
            elif ls_type in ("FIXED", "AT_LEAST", "ATLEAST"):
                px = _hwpunit_to_px(val)
                if px:
                    decl["line-height"] = px
        margin = _first(pp, "margin")
        if margin is not None:
            for mc in margin:
                px = _hwpunit_to_px(mc.get("value"))
                if not px:
                    continue  # 0·음수(내어쓰기)는 생략
                mtag = tag(mc)
                if mtag == "intent":
                    decl["text-indent"] = px
                elif mtag == "left":
                    decl["padding-left"] = px
                elif mtag == "right":
                    decl["padding-right"] = px
                elif mtag == "prev":
                    decl["margin-top"] = px
                elif mtag == "next":
                    decl["margin-bottom"] = px
        if decl:
            hs.para_css[pid] = _build_decl(decl)

    # hh:style → paraPr/charPr 참조 (문단 styleIDRef 해석용)
    for st in root.iter():
        if tag(st) != "style":
            continue
        sid = st.get("id")
        if sid is None:
            continue
        if (st.get("type") or "").upper() == "PARA":
            hs.style_refs[sid] = (st.get("paraPrIDRef"), st.get("charPrIDRef"))
        else:
            hs.style_refs[sid] = (None, st.get("charPrIDRef"))

    return hs


def _class_css(hs: _HeaderStyles) -> str:
    """헤더 스타일 dict들을 CSS 클래스 규칙 텍스트로 만든다."""

    def _key(item: tuple[str, str]) -> tuple[int, int | str]:
        k = item[0]
        return (0, int(k)) if k.isdigit() else (1, k)

    lines: list[str] = []
    for cid, decl in sorted(hs.char_css.items(), key=_key):
        lines.append(f".ch{cid} {{ {decl} }}")
    for pid, decl in sorted(hs.para_css.items(), key=_key):
        lines.append(f".pa{pid} {{ {decl} }}")
    for bid, decl in sorted(hs.border_css.items(), key=_key):
        lines.append(f".bf{bid} {{ {decl} }}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# BinData 이미지
# ---------------------------------------------------------------------------

def _image_mime(data: bytes) -> str:
    """이미지 바이트의 매직 넘버로 MIME 타입을 판정한다."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/png"


def _load_bindata(extract_dir: Path) -> dict[str, bytes]:
    """BinData 폴더의 이미지를 {참조 키: 바이트}로 적재한다.

    키는 content.hpf 매니페스트의 item id와 파일명 stem 둘 다 등록한다
    (hc:img@binaryItemIDRef가 어느 쪽을 가리켜도 찾을 수 있게).
    """
    id_to_href: dict[str, str] = {}
    hpf = extract_dir / "Contents" / "content.hpf"
    if hpf.is_file():
        try:
            for item in ET.parse(str(hpf)).getroot().iter():
                if tag(item) == "item":
                    iid, href = item.get("id"), item.get("href")
                    if iid and href:
                        id_to_href[iid] = href.replace("\\", "/").lower()
        except ET.ParseError:
            pass

    images: dict[str, bytes] = {}
    for sub in ("BinData", "Contents/BinData"):
        bin_dir = extract_dir / sub
        if not bin_dir.is_dir():
            continue
        for fp in sorted(bin_dir.iterdir()):
            if not fp.is_file() or fp.suffix.lower() not in _IMG_EXTS:
                continue
            try:
                data = fp.read_bytes()
            except OSError:
                continue
            rel = fp.relative_to(extract_dir).as_posix().lower()
            images.setdefault(fp.stem, data)
            for iid, href in id_to_href.items():
                if href == rel or href.endswith("/" + fp.name.lower()):
                    images.setdefault(iid, data)
    return images


def _render_pic(pic: ET.Element, img_map: dict[str, bytes]) -> str:
    """hp:pic을 base64 data URI <img>로 렌더한다. 이미지를 못 찾으면 빈 문자열."""
    img_elem = next((e for e in pic.iter() if tag(e) == "img"), None)
    if img_elem is None:
        return ""
    data = img_map.get(img_elem.get("binaryItemIDRef") or "")
    if not data:
        return ""
    style = ["max-width:100%"]
    sz = _first(pic, "sz")
    if sz is not None:
        w = _hwpunit_to_px(sz.get("width"))
        h = _hwpunit_to_px(sz.get("height"))
        if w:
            style.append(f"width:{w}")
        if h:
            style.append(f"height:{h}")
    b64 = base64.b64encode(data).decode("ascii")
    return f'<img src="data:{_image_mime(data)};base64,{b64}" style="{";".join(style)}" alt=""/>'


# ---------------------------------------------------------------------------
# data-id 매핑 (핵심 불변식)
# ---------------------------------------------------------------------------

def _map_node_ids(
    root: ET.Element, nodes: list,
) -> tuple[dict[ET.Element, int], dict[ET.Element, int]]:
    """parse_section 노드 목록을 동일 순회 순서로 소비해 요소→로컬 id 매핑을 만든다.

    id를 여기서 새로 매기지 않고 parse_section이 매긴 id를 그대로 쓴다.
    순회 순서가 parse_section과 어긋나면 type 불일치·잔여 노드로 즉시 RuntimeError.
    반환: ({hp:p 요소: id}, {hp:tc 요소: id})
    """
    it = iter(nodes)
    p_ids: dict[ET.Element, int] = {}
    tc_ids: dict[ET.Element, int] = {}

    def _take(expected: str, where: str) -> int:
        node = next(it, None)
        if node is None or node.type != expected:
            got = "노드 없음" if node is None else f"type={node.type}(id={node.id})"
            raise RuntimeError(
                f"data-id 매핑이 parse_section 순번 규칙과 어긋남: {where}에서 {expected} 기대, {got}"
            )
        return node.id

    def _walk_table(tbl_elem: ET.Element) -> None:
        for tr in tbl_elem:
            if tag(tr) != "tr":
                continue
            for tc in tr:
                if tag(tc) != "tc":
                    continue
                nested = _find_toplevel_tables(tc)
                if nested:  # 중첩 표 셀: 외부 셀은 노드가 아님, 내부 표 셀만
                    for nested_tbl in nested:
                        _walk_table(nested_tbl)
                    continue
                tc_ids[tc] = _take("table_cell", "표 셀")

    for child in root:
        if tag(child) != "p":
            continue
        # 문단 직계 run의 직계 tbl 먼저 (표 셀이 문단보다 먼저 id를 받음)
        for run in child:
            if tag(run) != "run":
                continue
            for gc in run:
                if tag(gc) == "tbl":
                    _walk_table(gc)
        runs, _t_elems = collect_runs_and_texts(child)
        if runs:
            p_ids[child] = _take("body_text", "문단")

    leftover = next(it, None)
    if leftover is not None:
        raise RuntimeError(
            f"data-id 매핑이 parse_section 순번 규칙과 어긋남: 소비되지 않은 노드 id={leftover.id}"
        )
    return p_ids, tc_ids


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------

@dataclass
class _RenderCtx:
    """섹션 하나를 렌더링하는 동안 공유하는 컨텍스트."""

    header: _HeaderStyles
    img_map: dict[str, bytes]
    p_ids: dict[ET.Element, int]
    tc_ids: dict[ET.Element, int]
    offset: int  # 이전 섹션들의 노드 수 합 (전역 id = 로컬 id + offset)


def _cls_attr(prefix: str, ref: str | None, css: dict[str, str]) -> str:
    """스타일 참조 id가 유효하면 class 속성 문자열을, 아니면 빈 문자열을 반환한다."""
    rid = _safe_id(ref)
    if rid and rid in css:
        return f' class="{prefix}{rid}"'
    return ""


def _render_paragraph(p_elem: ET.Element, ctx: _RenderCtx) -> str:
    """hp:p 하나를 렌더한다. 표를 만나면 <p>를 끊고 <table>을 사이에 끼운다.

    노드인 문단(p_ids에 있음)은 data-id를 정확히 한 번 출력한다
    — 텍스트가 없어도(표만 있는 문단 등) 빈 <p data-id>를 남긴다.
    """
    hs = ctx.header
    base_para, base_char = hs.style_refs.get(p_elem.get("styleIDRef") or "", (None, None))
    p_cls = _cls_attr("pa", p_elem.get("paraPrIDRef") or base_para, hs.para_css)
    local_id = ctx.p_ids.get(p_elem)
    id_attr = f' data-id="{local_id + ctx.offset}"' if local_id is not None else ""

    parts: list[str] = []
    segments: list[str] = []
    id_emitted = False

    def _flush(force: bool = False) -> None:
        nonlocal id_emitted
        if not segments and not force:
            return
        attr = ""
        if id_attr and not id_emitted:
            attr = id_attr
            id_emitted = True
        parts.append(f"<p{attr}{p_cls}>{''.join(segments) or '&nbsp;'}</p>")
        segments.clear()

    for run in p_elem:
        if tag(run) != "run":
            continue
        span_cls = _cls_attr("ch", run.get("charPrIDRef") or base_char, hs.char_css)
        for child in run:
            ctag = tag(child)
            if ctag == "t":
                text = escape(child.text or "")
                if text:
                    segments.append(f"<span{span_cls}>{text}</span>" if span_cls else text)
            elif ctag == "tbl":
                _flush()
                parts.append(_render_table(child, ctx))
            elif ctag == "pic":
                img = _render_pic(child, ctx.img_map)
                if img:
                    segments.append(img)
            # 그 외(secPr·ctrl·도형·수식·각주 등)는 렌더 생략

    _flush(force=bool(id_attr) and not id_emitted)
    if not parts:
        # 노드가 아닌 빈 문단도 줄 간격 유지를 위해 출력
        parts.append(f"<p{p_cls}>&nbsp;</p>")
    return "".join(parts)


def _render_cell(tc: ET.Element, ctx: _RenderCtx) -> str:
    """hp:tc → <td>. 병합으로 사라진 자리의 셀은 hwpx XML에 tc로 존재하지 않으므로
    colspan/rowspan 속성만 반영하면 표 구조가 유지된다."""
    cls = _cls_attr("bf", tc.get("borderFillIDRef"), ctx.header.border_css)
    local_id = ctx.tc_ids.get(tc)
    id_attr = f' data-id="{local_id + ctx.offset}"' if local_id is not None else ""

    style: list[str] = []
    span_attr = ""
    sub: ET.Element | None = None
    for child in tc:
        ctag = tag(child)
        if ctag == "cellSz":
            w = _hwpunit_to_px(child.get("width"))
            h = _hwpunit_to_px(child.get("height"))
            if w:
                style.append(f"width:{w}")
            if h:
                style.append(f"height:{h}")
        elif ctag == "cellSpan":
            col = child.get("colSpan")
            row = child.get("rowSpan")
            if col and col != "1":
                span_attr += f' colspan="{col}"'
            if row and row != "1":
                span_attr += f' rowspan="{row}"'
        elif ctag == "subList":
            sub = child

    content = ""
    if sub is not None:
        va = (sub.get("vertAlign") or "").upper()
        if va == "CENTER":
            style.append("vertical-align:middle")
        elif va == "BOTTOM":
            style.append("vertical-align:bottom")
        content = "".join(_render_paragraph(p, ctx) for p in sub if tag(p) == "p")
    if not content:
        content = "&nbsp;"
    style_attr = f' style="{";".join(style)}"' if style else ""
    return f"<td{id_attr}{cls}{span_attr}{style_attr}>{content}</td>"


def _render_table(tbl_elem: ET.Element, ctx: _RenderCtx) -> str:
    """hp:tbl → <table>. 행은 직계 hp:tr, 셀은 직계 hp:tc만 순회한다."""
    tbl_style: list[str] = []
    sz = _first(tbl_elem, "sz")
    if sz is not None:
        w = _hwpunit_to_px(sz.get("width"))
        if w:
            tbl_style.append(f"width:{w}")
    pos = _first(tbl_elem, "pos")
    if pos is not None and (pos.get("horzAlign") or "").upper() == "CENTER":
        tbl_style.append("margin-left:auto")
        tbl_style.append("margin-right:auto")
    tbl_cls = _cls_attr("bf", tbl_elem.get("borderFillIDRef"), ctx.header.border_css)
    style_attr = f' style="{";".join(tbl_style)}"' if tbl_style else ""

    rows: list[str] = []
    for tr in tbl_elem:
        if tag(tr) != "tr":
            continue
        cells = [_render_cell(tc, ctx) for tc in tr if tag(tc) == "tc"]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table{tbl_cls}{style_attr}>{''.join(rows)}</table>"


def _section_page_style(root: ET.Element) -> str:
    """섹션의 hp:pagePr(페이지 크기·여백)을 .page div 인라인 스타일로 변환한다."""
    page_pr = next((e for e in root.iter() if tag(e) == "pagePr"), None)
    if page_pr is None:
        return ""
    style: list[str] = []
    w = _hwpunit_to_px(page_pr.get("width"))
    h = _hwpunit_to_px(page_pr.get("height"))
    if w:
        style.append(f"width:{w}")
    if h:
        style.append(f"min-height:{h}")  # 섹션에 여러 실제 페이지가 담길 수 있어 고정 height 금지
    margin = _first(page_pr, "margin")
    if margin is not None:
        top = _hwpunit_to_px(margin.get("top")) or "0"
        right = _hwpunit_to_px(margin.get("right")) or "0"
        bottom = _hwpunit_to_px(margin.get("bottom")) or "0"
        left = _hwpunit_to_px(margin.get("left")) or "0"
        style.append(f"padding:{top} {right} {bottom} {left}")
    return ";".join(style)


# ---------------------------------------------------------------------------
# 문서 조립
# ---------------------------------------------------------------------------

# td 기본 border는 borderFill 클래스(.bfN — 4변을 항상 명시)가 우선 적용되어 덮어쓴다.
_BASE_CSS = """\
body { margin: 0; padding: 16px 8px; background: #e9e9e9;
       font-family: "맑은 고딕", "Malgun Gothic", sans-serif; line-height: 1.4; }
.page { background: #fff; margin: 0 auto 16px auto; box-sizing: border-box;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25); overflow-x: auto; }
p { margin: 0; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 2px 0; max-width: 100%; }
td { border: 1px solid #999; vertical-align: top; padding: 1px 4px;
     box-sizing: border-box; word-break: break-all; }
img { max-width: 100%; }"""


def _assemble_document(title: str, class_css: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        "<style>\n"
        f"{_BASE_CSS}\n"
        f"{class_css}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def hwpx_to_html(
    hwpx_path: str | Path, *, inject_ids: bool = True, split_pages: bool = True,
) -> HtmlResult:
    """hwpx 파일을 페이지 분할된 self-contained HTML 문자열로 변환한다.

    - inject_ids: True면 문단·표 셀에 전역 data-id를 주입한다
      (순번은 parser.parse_section 규칙과 동일 — 모듈 docstring 참조).
    - split_pages: True면 섹션마다 .page div로 분할, False면 전체를 .page 하나에 담는다.
    파일을 쓰지 않고 HtmlResult(html, page_count, node_count)를 반환한다.
    원본 hwpx는 수정하지 않는다(임시 디렉터리에 해제해 읽기만 함).
    """
    hwpx_path = Path(hwpx_path)
    sections_html: list[tuple[str, str]] = []  # (페이지 스타일, 본문 블록)
    node_total = 0

    with tempfile.TemporaryDirectory(prefix="hwpx2html_", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        extract_hwpx(hwpx_path, tmp_path)
        header = _parse_header(tmp_path / "Contents" / "header.xml")
        img_map = _load_bindata(tmp_path)

        for sec_path in find_section_files(tmp_path):
            nodes, tree, _parent_map, _t_ns = parse_section(sec_path)
            root = tree.getroot()
            if inject_ids:
                p_ids, tc_ids = _map_node_ids(root, nodes)
            else:
                p_ids, tc_ids = {}, {}
            ctx = _RenderCtx(header, img_map, p_ids, tc_ids, offset=node_total)
            blocks = "".join(
                _render_paragraph(child, ctx) for child in root if tag(child) == "p"
            )
            sections_html.append((_section_page_style(root), blocks))
            node_total += len(nodes)

    if split_pages:
        body = "".join(
            f'<div class="page" style="{style}">{content}</div>'
            for style, content in sections_html
        )
        page_count = len(sections_html)
    else:
        first_style = sections_html[0][0] if sections_html else ""
        content = "".join(c for _, c in sections_html)
        body = f'<div class="page" style="{first_style}">{content}</div>'
        page_count = 1

    html_doc = _assemble_document(hwpx_path.name, _class_css(header), body)
    return HtmlResult(html=html_doc, page_count=page_count, node_count=node_total)
