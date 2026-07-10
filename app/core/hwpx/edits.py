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
"""
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .package import extract_hwpx, find_section_files, repack_hwpx
from .parser import parse_section
from .xml_utils import register_namespaces

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
      (존재하지 않는 id · 원본과 동일한 텍스트 · hp:t가 전혀 없는 노드)
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
            nodes, tree, _parent_map, _t_ns = parse_section(sf)
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
                    result.skipped_ids.append(gid)  # hp:t 없는 빈 셀 → 1차 범위 밖
                    continue
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
