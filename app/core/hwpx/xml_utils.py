"""hwpx XML 공통 헬퍼.

ElementTree는 태그를 "{네임스페이스URI}로컬명" 형태로 다루므로,
네임스페이스를 무시하고 로컬명으로 다루는 `tag()`/`ns()` 헬퍼와,
직렬화 시 원본 프리픽스(hp/hs/hc 등)를 보존하기 위한
`register_namespaces()`를 제공한다.

주의: 프리픽스 등록 없이 ET로 저장하면 ns0: 같은 자동 프리픽스가 생겨
한컴오피스에서 문서가 열리지 않을 수 있다. 반드시 `ET.parse()` 전에
`register_namespaces()`를 호출할 것.
"""
from pathlib import Path
from xml.etree import ElementTree as ET

# OWPML(hwpx) 표준 네임스페이스 프리픽스 목록.
# 파일에 선언되지 않은 URI가 나중에 추가되더라도 표준 프리픽스로 직렬화되도록 미리 등록한다.
HWPX_NAMESPACES: dict[str, str] = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "hv": "http://www.hancom.co.kr/hwpml/2011/version",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf/",
    "epub": "http://www.idpf.org/2007/ops",
}


def tag(elem: ET.Element) -> str:
    """네임스페이스를 제거한 로컬 태그명을 반환한다. 예: "{...}p" → "p"."""
    if "}" in elem.tag:
        return elem.tag.split("}", 1)[1]
    return elem.tag


def ns(elem: ET.Element) -> str:
    """"{URI}" 형태의 네임스페이스 접두부를 반환한다(없으면 빈 문자열)."""
    if elem.tag.startswith("{"):
        return elem.tag.split("}", 1)[0] + "}"
    return ""


def register_namespaces(xml_path: str | Path) -> None:
    """표준 hwpx 프리픽스와 해당 XML 파일이 선언한 프리픽스를 전역 등록한다.

    파일에 선언된 프리픽스가 표준 목록과 다르면 파일 쪽이 우선한다
    (나중에 등록한 것이 이긴다). `ET.parse()` 이전에 호출해야
    이후 `tree.write()` 시 원본 프리픽스가 보존된다.
    """
    for prefix, uri in HWPX_NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    for _event, elem in ET.iterparse(str(xml_path), events=("start-ns",)):
        prefix, uri = elem
        ET.register_namespace(prefix or "", uri)
