"""header.xml 스타일 해석 — 작성 가이드(charPr) 감지 (B1).

정부 R&D 표준 서식류는 채울 곳을 "파란색 기울임체 작성 가이드" 문구로
표시한다(서식1 실측: 가이드 charPr 61종, run 264개·5,891자). regex
placeholder만으로는 이 채움 지시가 전혀 탐지되지 않으므로, header.xml의
charPr를 해석해 가이드 스타일 id 집합을 만들고 parse_section에 넘긴다.

판정 규칙 (실측 검증: 서식1 61개 적중, 실양식·템플릿 3종 오탐 0):
- 기울임: charPr 자손에 <hh:italic> 존재
- 파란 계열: textColor "#RRGGBB"에서 B>=128 이고 B가 R·G보다 40 이상 큼
"""
from pathlib import Path
from xml.etree import ElementTree as ET

from .xml_utils import tag


def _is_blueish(color: str | None) -> bool:
    """textColor가 파란 계열(#RRGGBB, B 우세)인지 판정한다."""
    if not color or not color.startswith("#") or len(color) != 7:
        return False
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
    except ValueError:
        return False
    return b >= 128 and b > r + 40 and b > g + 40


def guide_char_pr_ids(header_path: str | Path) -> set[str]:
    """header.xml에서 '기울임 + 파란 계열' charPr id 집합을 반환한다.

    header가 없거나 파싱 불가면 빈 집합 (가이드 감지 없이 기존 동작 유지).
    """
    header_path = Path(header_path)
    if not header_path.is_file():
        return set()
    try:
        root = ET.parse(header_path).getroot()
    except ET.ParseError:
        return set()

    ids: set[str] = set()
    for char_pr in root.iter():
        if tag(char_pr) != "charPr":
            continue
        cp_id = char_pr.get("id")
        if cp_id is None:
            continue
        color = None
        for k, v in char_pr.attrib.items():
            if k.split("}")[-1] == "textColor":
                color = v
                break
        if not _is_blueish(color):
            continue
        if any(tag(child) == "italic" for child in char_pr.iter()):
            ids.add(cp_id)
    return ids
