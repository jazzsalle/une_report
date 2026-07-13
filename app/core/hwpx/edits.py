"""편집 명세(edits) → 원본 hwpx 부분 수정 → 새 hwpx 재패키징 (M1-4·M1-5).

편집 명세는 `[{"id": <전역 노드 id>, "new_text": "..."}]` 형태다.
전역 id는 "섹션 로컬 id(parse_section 순번) + 이전 섹션들의 노드 수 누적"으로,
T2의 HTML 변환기가 data-id로 노출하는 값과 동일한 체계다.

텍스트 교체 규칙(서식 보존):
- 노드의 첫 hp:t에 new_text를 넣고 나머지 hp:t는 빈 문자열로 만든다.
  첫 run의 charPr가 유지되므로 글자 서식이 보존된다.
- hp:t 내부의 자식 요소(hp:tab 등)와 그 tail 텍스트는 제거한다
  (남겨두면 옛 텍스트 조각이 tail로 살아남는다).
- new_text가 원본과 공백 제거 후 동일하면 XML을 건드리지 않고 skip한다.
- new_text의 개행(\\n)은 1차 범위에서 문단 분할 없이 공백으로 합쳐
  하나의 hp:t 안에서 처리한다(문단 복제 방식은 추후 확장).

빈 노드(hp:t 없음) 채움 규칙 — 실양식의 빈 표 셀 대응:
- 컨테이너(node.elem) 안에 기존 hp:run이 있으면(실측상 지배적: 한컴
  빈 셀은 run만 있고 t가 없다) 그 첫 run에 hp:t를 추가한다.
  run의 charPrIDRef가 그대로 적용되므로 글자 서식이 보존된다.
- run이 없으면 컨테이너의 첫 hp:p에 hp:run+hp:t를 생성한다.
  charPrIDRef는 섹션에서 처음 발견되는 run의 값을 복제하고, 없으면
  속성을 생략한다(한컴 기본 서식). 문단 서식은 기존 hp:p의
  paraPrIDRef가 그대로 유지된다.
- hp:p조차 없는 컨테이너는 종전대로 skip한다.

레이아웃 캐시(linesegarray) 무효화 — 문장 겹침 방지:
- 한컴오피스는 문서를 열 때 문단의 hp:linesegarray(줄배치 캐시)를 재사용한다.
  텍스트만 바꾸고 캐시를 남기면 옛 텍스트(줄 수) 기준 좌표에 긴 새 텍스트가
  그려져 문장이 겹친다 (python-hwpx body_patch.py 실측 기록과 동일 증상,
  ouputs/문장겹침_원인분석_개선안.md 참조).
- 따라서 편집(교체·삽입)된 노드가 속한 hp:p의 linesegarray를 제거한다.
  캐시가 없으면 한컴이 열 때 재계산하므로 정상 렌더링된다.
- 편집하지 않은 문단의 캐시는 보존한다(불필요한 재계산 방지).
"""
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import TextNode
from .package import extract_hwpx, find_section_files, repack_hwpx
from .parser import collect_runs_and_texts, parse_section
from .xml_utils import HWPX_NAMESPACES, register_namespaces, tag

# t_ns를 문서에서 못 얻을 때(문서 전체에 hp:t가 0개) 쓰는 표준 네임스페이스
_HP_NS = "{" + HWPX_NAMESPACES["hp"] + "}"

# "p-0012" 같은 접두어 붙은 id에서 끝자리 숫자를 뽑는 패턴
_TRAILING_DIGITS_RE = re.compile(r"(\d+)\s*$")


def normalize_edit_id(x) -> int:
    """편집 대상 id를 정수로 정규화한다. 12, "12", "p-0012" 모두 허용.

    음수·해석 불가 값은 ValueError를 던진다.
    """
    if isinstance(x, bool):
        raise ValueError(f"편집 id로 bool은 허용되지 않음: {x!r}")
    if isinstance(x, int):
        if x < 0:
            raise ValueError(f"편집 id는 0 이상이어야 함: {x!r}")
        return x
    if isinstance(x, str):
        s = x.strip()
        if s.isdigit():
            return int(s)
        m = _TRAILING_DIGITS_RE.search(s)
        if m:
            return int(m.group(1))
    raise ValueError(f"편집 id를 정수로 해석할 수 없음: {x!r}")


@dataclass
class EditResult:
    """apply_edits 수행 결과.

    - applied_ids: 실제 XML이 변경된 전역 id 목록
    - skipped_ids: 건너뛴 전역 id 목록
      (존재하지 않는 id · 원본과 동일한 텍스트 · 삽입 앵커(hp:p)조차 없는 빈 노드)
    - output_path: 재패키징된 hwpx 경로
    - section_snapshots: 편집된 섹션 XML 사본 경로 목록
      (output 옆 "<출력파일명 stem>_sections/" 아래, DB 연동 대비)
    """

    applied_ids: list[int] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)
    output_path: str = ""
    section_snapshots: list[str] = field(default_factory=list)


def _norm_ws(text: str | None) -> str:
    """공백류를 모두 제거해 '실질 텍스트 동일' 비교용 문자열을 만든다."""
    return re.sub(r"\s+", "", text or "")


def _flatten_newlines(text: str) -> str:
    """개행 포함 텍스트를 하나의 hp:t에 넣을 수 있게 한 줄로 합친다."""
    if "\n" not in text:
        return text
    lines = [ln.strip() for ln in text.split("\n")]
    return " ".join(ln for ln in lines if ln)


def _set_node_text(node, new_text: str) -> None:
    """노드의 hp:t들에 새 텍스트를 기록한다(첫 t에 전체, 나머지는 비움)."""
    for i, t in enumerate(node.t_elems):
        # hp:t 내부 자식(hp:tab 등)과 tail을 제거해 옛 텍스트 잔존을 막는다
        for child in list(t):
            t.remove(child)
        t.text = new_text if i == 0 else ""


def _nearest_p(elem: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> ET.Element | None:
    """elem에서 부모 방향으로 올라가며 가장 가까운 hp:p를 찾는다. 없으면 None."""
    cur: ET.Element | None = elem
    while cur is not None:
        if tag(cur) == "p":
            return cur
        cur = parent_map.get(cur)
    return None


def _strip_lineseg_cache(p_elem: ET.Element) -> None:
    """문단의 hp:linesegarray(한컴 줄배치 캐시)를 제거한다.

    편집된 문단에 stale 캐시가 남으면 한컴이 옛 줄배치를 재사용해
    문장이 겹쳐 렌더된다. 제거하면 열 때 재계산된다(모듈 docstring 참조).
    """
    for child in list(p_elem):
        if tag(child) == "linesegarray":
            p_elem.remove(child)


def _find_first_p(elem: ET.Element) -> ET.Element | None:
    """컨테이너 안의 첫 hp:p를 찾는다(표 내부 제외). elem 자신이 p면 그대로."""
    if tag(elem) == "p":
        return elem
    for child in elem:
        if tag(child) == "tbl":
            continue
        found = _find_first_p(child)
        if found is not None:
            return found
    return None


def _ensure_t_elem(
    node: TextNode,
    t_ns: str,
    fallback_char_ref: str | None,
    parent_map: dict[ET.Element, ET.Element],
) -> ET.Element | None:
    """빈 노드(hp:t 없음)에 hp:t를 생성 삽입해 반환한다. 앵커가 없으면 None.

    모듈 docstring "빈 노드 채움 규칙" 참조. t_ns가 비어 있으면(문서 전체에
    hp:t가 0개) 표준 hp 네임스페이스로 생성한다.
    삽입된 문단의 linesegarray는 제거한다(stale 캐시 → 문장 겹침 방지).
    """
    if node.elem is None:
        return None
    ns = t_ns or _HP_NS

    # 1) 기존 run이 있으면 그 안에 t 추가 (charPrIDRef 서식 그대로 적용)
    runs, _ts = collect_runs_and_texts(node.elem)
    if runs:
        p = _nearest_p(runs[0], parent_map)
        if p is not None:
            _strip_lineseg_cache(p)
        return ET.SubElement(runs[0], f"{ns}t")

    # 2) run이 없으면 첫 hp:p에 run+t 생성
    p = _find_first_p(node.elem)
    if p is None:
        return None
    run = ET.Element(f"{ns}run")
    if fallback_char_ref:
        run.set("charPrIDRef", fallback_char_ref)
    insert_at = len(p)
    for i, child in enumerate(p):
        if tag(child) == "linesegarray":
            insert_at = i
            break
    p.insert(insert_at, run)
    _strip_lineseg_cache(p)
    return ET.SubElement(run, f"{ns}t")


def _first_char_pr_ref(root: ET.Element) -> str | None:
    """섹션에서 처음 발견되는 run의 charPrIDRef 값 (run 없는 문단 채움용)."""
    for elem in root.iter():
        if tag(elem) == "run":
            ref = elem.get("charPrIDRef")
            if ref:
                return ref
    return None


def apply_edits(
    hwpx_path: str | Path,
    edits: list[dict],
    output_path: str | Path,
) -> EditResult:
    """edits를 원본 hwpx에 적용해 output_path로 새 hwpx를 만든다.

    edits: [{"id": 12 | "12" | "p-0012", "new_text": "..."}]
    - id는 전역 id(섹션 로컬 id + 이전 섹션 노드 수 누적)
    - 같은 id가 중복되면 마지막 항목이 이긴다
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    edit_map: dict[int, str] = {}
    for e in edits:
        gid = normalize_edit_id(e["id"])
        edit_map[gid] = e.get("new_text") or ""

    result = EditResult(output_path=str(output_path))
    snapshot_dir = output_path.parent / f"{output_path.stem}_sections"

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "hwpx"
        compress_info, file_order = extract_hwpx(hwpx_path, extract_dir)
        section_files = find_section_files(extract_dir)
        if not section_files:
            raise RuntimeError(f"섹션 파일을 찾을 수 없음: {hwpx_path}")

        remaining = dict(edit_map)
        global_offset = 0
        for sf in section_files:
            nodes, tree, parent_map, t_ns = parse_section(sf)
            fallback_char_ref = None  # 필요해질 때 1회만 탐색 (섹션 단위 캐시)
            changed = False
            for node in nodes:
                gid = global_offset + node.id
                if gid not in remaining:
                    continue
                new_text = remaining.pop(gid)
                if _norm_ws(node.raw_text) == _norm_ws(new_text):
                    result.skipped_ids.append(gid)  # 실질 동일 → XML 무변경
                    continue
                if not node.t_elems:
                    # hp:t 없는 빈 노드(실양식 빈 셀) → hp:t 생성 삽입 후 채움
                    if fallback_char_ref is None:
                        fallback_char_ref = _first_char_pr_ref(tree.getroot()) or ""
                    new_t = _ensure_t_elem(node, t_ns, fallback_char_ref, parent_map)
                    if new_t is None:
                        result.skipped_ids.append(gid)  # 삽입 앵커(hp:p) 없음
                        continue
                    node.t_elems = [new_t]
                else:
                    # 편집된 문단의 줄배치 캐시 제거 (stale → 문장 겹침)
                    for t in node.t_elems:
                        p = _nearest_p(t, parent_map)
                        if p is not None:
                            _strip_lineseg_cache(p)
                _set_node_text(node, _flatten_newlines(new_text))
                result.applied_ids.append(gid)
                changed = True

            if changed:
                # 프리픽스 보존: 등록 없이 쓰면 ns0: 프리픽스로 한컴오피스에서 안 열림
                register_namespaces(sf)
                tree.write(sf, xml_declaration=True, encoding="utf-8")
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                dest = snapshot_dir / sf.name
                shutil.copy2(sf, dest)
                result.section_snapshots.append(str(dest))

            global_offset += len(nodes)

        # 어느 섹션에도 없는 id
        result.skipped_ids.extend(sorted(remaining))
        repack_hwpx(extract_dir, output_path, compress_info, file_order)

    return result
