"""hwpx zip 패키징 계층: 해제·재압축·구조 검증.

hwpx는 OCF(zip) 컨테이너다. 한컴오피스가 다시 열 수 있으려면
재압축 시 다음을 보존해야 한다.
- 파일 순서(특히 mimetype이 첫 엔트리)
- 엔트리별 압축 방식(mimetype은 무압축 STORED)
편집으로 내용이 바뀌면 서명이 무효가 되므로 META-INF의 서명 관련
엔트리는 재압축에서 제외한다.
"""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import ValidationResult

# Contents/section0.xml, section1.xml ... 매칭 (번호 없는 section.xml도 허용)
_SECTION_RE = re.compile(r"^section(\d*)\.xml$", re.IGNORECASE)

# META-INF 아래에서 재압축 시 제외할 서명·암호화 관련 파일명 키워드
_SIGNATURE_KEYWORDS = ("signature", "sign", "xmlsig", "encrypt", "certif")

# validate_hwpx에서 XML 파스 가능 여부를 확인하는 필수 파일
_REQUIRED_XML_ENTRIES = ("Contents/header.xml", "Contents/content.hpf")


def extract_hwpx(hwpx_path: str | Path, extract_dir: str | Path) -> tuple[dict[str, int], list[str]]:
    """hwpx zip을 해제하고 (엔트리별 압축 방식, 엔트리 순서)를 반환한다.

    반환값은 repack_hwpx에 그대로 넘겨 원본 패키징 특성을 보존하는 데 쓴다.
    - compress_info: {엔트리명: compress_type(ZIP_STORED/ZIP_DEFLATED)}
    - file_order: 원본 zip의 엔트리 순서
    """
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        infos = zf.infolist()
        compress_info = {info.filename: info.compress_type for info in infos}
        file_order = [info.filename for info in infos]
        zf.extractall(extract_dir)
    return compress_info, file_order


def find_section_files(extract_dir: str | Path) -> list[Path]:
    """해제 디렉터리에서 Contents/section*.xml 경로들을 섹션 번호순으로 반환한다."""
    contents_dir = Path(extract_dir) / "Contents"
    if not contents_dir.is_dir():
        return []

    def _section_no(path: Path) -> tuple[int, str]:
        m = _SECTION_RE.match(path.name)
        num = int(m.group(1)) if m and m.group(1) else -1
        return (num, path.name)

    sections = [p for p in contents_dir.iterdir() if p.is_file() and _SECTION_RE.match(p.name)]
    return sorted(sections, key=_section_no)


def _is_signature_entry(rel: str) -> bool:
    """META-INF 아래 서명·암호화 관련 엔트리인지 판정한다."""
    parts = rel.lower().split("/")
    if len(parts) < 2 or parts[0] != "meta-inf":
        return False
    return any(k in parts[-1] for k in _SIGNATURE_KEYWORDS)


def repack_hwpx(
    extract_dir: str | Path,
    output_path: str | Path,
    compress_info: dict[str, int] | None = None,
    file_order: list[str] | None = None,
) -> None:
    """해제 디렉터리를 hwpx zip으로 재압축한다.

    - file_order가 있으면 원본 엔트리 순서를 따르고, 새로 생긴 파일은 뒤에 붙인다.
    - compress_info가 있으면 엔트리별 원본 압축 방식을 따른다.
    - mimetype은 정보가 없어도 항상 무압축(STORED)·첫 엔트리로 기록한다.
    - META-INF의 서명 관련 엔트리는 제외한다(편집으로 서명이 무효가 되므로).
    """
    extract_dir = Path(extract_dir)
    output_path = Path(output_path)
    if output_path.exists():
        output_path.unlink()

    # 해제 디렉터리의 전체 파일 목록 (엔트리명 → 실제 경로)
    all_files: dict[str, Path] = {}
    for fp in extract_dir.rglob("*"):
        if fp.is_file():
            rel = fp.relative_to(extract_dir).as_posix()
            all_files[rel] = fp

    if file_order:
        ordered = [rel for rel in file_order if rel in all_files]
        known = set(file_order)
        ordered += [rel for rel in all_files if rel not in known]
    else:
        ordered = sorted(all_files)
        if "mimetype" in all_files:
            ordered.remove("mimetype")
            ordered.insert(0, "mimetype")

    def _compress_type(rel: str) -> int:
        if rel == "mimetype":
            return zipfile.ZIP_STORED
        if compress_info and rel in compress_info:
            return compress_info[rel]
        return zipfile.ZIP_DEFLATED

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in ordered:
            if _is_signature_entry(rel):
                continue
            zf.write(all_files[rel], rel, compress_type=_compress_type(rel))


def validate_hwpx(hwpx_path: str | Path) -> ValidationResult:
    """hwpx 파일의 최소 패키지 구조를 검증한다.

    검사 항목:
    1. zip으로 열림
    2. mimetype 엔트리 존재
    3. Contents/header.xml 존재
    4. Contents/section*.xml 1개 이상 존재
    5. Contents/content.hpf 존재
    6. 위 XML들(header·section들·content.hpf)이 모두 파스 가능
    """
    errors: list[str] = []
    checked: list[str] = []

    try:
        zf = zipfile.ZipFile(hwpx_path, "r")
    except (zipfile.BadZipFile, OSError) as e:
        return ValidationResult(ok=False, errors=[f"zip 열기 실패: {e}"], checked=["zip_open"])

    with zf:
        checked.append("zip_open")
        names = set(zf.namelist())

        checked.append("mimetype")
        if "mimetype" not in names:
            errors.append("mimetype 엔트리가 없음")

        section_entries = sorted(
            n for n in names
            if n.startswith("Contents/") and _SECTION_RE.match(n.rsplit("/", 1)[-1])
        )
        checked.append("Contents/section*.xml")
        if not section_entries:
            errors.append("Contents/section*.xml이 하나도 없음")

        xml_entries = list(_REQUIRED_XML_ENTRIES) + section_entries
        for entry in _REQUIRED_XML_ENTRIES:
            checked.append(entry)
            if entry not in names:
                errors.append(f"{entry} 엔트리가 없음")

        for entry in xml_entries:
            if entry not in names:
                continue
            checked.append(f"xml_parse:{entry}")
            try:
                ET.fromstring(zf.read(entry))
            except ET.ParseError as e:
                errors.append(f"{entry} XML 파스 실패: {e}")

    return ValidationResult(ok=not errors, errors=errors, checked=checked)
