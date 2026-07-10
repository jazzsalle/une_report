"""미채움 placeholder 탐지·검증 (M1-6).

양식 문서에는 "[기관명]", "<담당자>", "YYYY년 MM월 DD일", "○○시설" 같은
채움 대상 표식(placeholder)이 들어 있다. 편집 파이프라인이 끝난 뒤에도
이런 표식이 남아 있으면 채움 실패 신호이므로, 노드 텍스트에서 패턴으로
탐지하고 출력 hwpx를 재파싱해 잔존/해소 여부를 리포트한다.
"""
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .models import TextNode
from .package import extract_hwpx, find_section_files
from .parser import parse_section

# 패턴명 → 정규식. 결과물에 남아 있으면 안 되는 일반적 placeholder 유형.
# (hp:t 텍스트만 대상으로 검사하므로 XML 태그와 섞이지 않는다)
PLACEHOLDER_PATTERNS: dict[str, re.Pattern] = {
    # [기관명], [연락처] 등 대괄호 표식 (한 줄, 40자 이내)
    "bracket": re.compile(r"\[[^\[\]\n]{1,40}\]"),
    # <담당자 이름> 등 꺾쇠 표식 (수식·비교연산 오탐을 줄이려 문자 집합 제한)
    "angle": re.compile(r"<[가-힣A-Za-z0-9 _·/\-]{1,30}>"),
    # 날짜 스텁: YYYY년, YY년, MM월, DD일
    "date_stub": re.compile(r"YYYY|YY년|MM월|DD일"),
    # 동그라미·X 채움 표식과 관용적 미정 표기
    "filler": re.compile(r"○○+|XX+|xx+|\bTBD\b|\bTODO\b"),
}


@dataclass
class PlaceholderHit:
    """탐지된 placeholder 1건.

    - id: 노드 id (verify_output에서는 전역 id)
    - text: 매칭된 placeholder 문자열
    - pattern: PLACEHOLDER_PATTERNS의 패턴명 (또는 "expected" — 원본 의심
      텍스트가 출력에 그대로 남은 경우)
    """

    id: int
    text: str
    pattern: str


@dataclass
class PlaceholderReport:
    """출력 hwpx의 placeholder 잔존/해소 리포트.

    - remaining: 출력에 아직 남아 있는 placeholder들
    - resolved: expected_replaced 중 출력에서 사라진(해소된) 텍스트들
    """

    remaining: list[PlaceholderHit] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)


def collect_placeholders(nodes: list[TextNode], id_offset: int = 0) -> list[PlaceholderHit]:
    """노드 목록에서 placeholder 패턴 매칭 결과를 모두 수집한다.

    id_offset은 다중 섹션 문서에서 전역 id를 만들 때 쓴다(기본 0 = 로컬 id).
    """
    hits: list[PlaceholderHit] = []
    for node in nodes:
        text = node.text
        if not text:
            continue
        for name, pattern in PLACEHOLDER_PATTERNS.items():
            for m in pattern.finditer(text):
                hits.append(PlaceholderHit(
                    id=id_offset + node.id, text=m.group(0), pattern=name,
                ))
    return hits


def verify_output(hwpx_path: str | Path, expected_replaced: set[str]) -> PlaceholderReport:
    """출력 hwpx를 재파싱해 placeholder 잔존/해소를 집계한다.

    - remaining: 일반 패턴 매칭 잔존분 + expected_replaced 중 출력 노드
      텍스트에 그대로 살아남은 것(pattern="expected", 중복 제외)
    - resolved: expected_replaced 중 출력 어디에도 없는 것 (정렬됨)
    """
    report = PlaceholderReport()
    all_texts: list[str] = []
    seen: set[tuple[int, str]] = set()

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "hwpx"
        extract_hwpx(hwpx_path, extract_dir)

        global_offset = 0
        for sf in find_section_files(extract_dir):
            nodes, _tree, _parent_map, _t_ns = parse_section(sf)

            # 1) 일반 패턴 잔존 (LLM이 새로 만든 placeholder 포함)
            for hit in collect_placeholders(nodes, id_offset=global_offset):
                seen.add((hit.id, hit.text))
                report.remaining.append(hit)

            # 2) 원본 의심 텍스트가 그대로 살아남았는가 (완전 포함 검사)
            for node in nodes:
                text = node.text
                if not text:
                    continue
                all_texts.append(text)
                gid = global_offset + node.id
                for expected in expected_replaced:
                    if expected and expected in text and (gid, expected) not in seen:
                        seen.add((gid, expected))
                        report.remaining.append(
                            PlaceholderHit(id=gid, text=expected, pattern="expected")
                        )

            global_offset += len(nodes)

    joined = "\n".join(all_texts)
    report.resolved = sorted(t for t in expected_replaced if t and t not in joined)
    return report
