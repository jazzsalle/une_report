"""hwpx 코어(M1) 테스트 공통 픽스처.

실측 대상 hwpx 파일 두 계열을 제공한다.
1. python-hwpx 라이브러리 동봉 샘플(Skeleton.hwpx, report_table.hwpx)
   — 실제 한컴 계열 도구로 만들어진 파일. 설치 경로는 하드코딩하지 않고
   `import hwpx` 후 패키지 디렉터리에서 동적으로 찾는다.
2. builder.build_hwpx(placeholders_demo=True)로 만든 placeholder 포함 양식
   — 편집·placeholder 검증 시나리오용.

모든 픽스처는 tmp_path에 사본을 만들어 반환하므로 원본을 오염시키지 않는다.
"""
import shutil
from pathlib import Path

import pytest

import hwpx as hwpx_lib
from app.core.hwpx import build_hwpx

# python-hwpx 설치 디렉터리 (동봉 샘플 hwpx 탐색 기준점)
_HWPX_PKG_DIR = Path(hwpx_lib.__file__).parent


def _copy_bundled(name: str, tmp_path: Path) -> Path:
    """python-hwpx 패키지 아래에서 name 파일을 찾아 tmp_path로 복사한다."""
    matches = sorted(_HWPX_PKG_DIR.rglob(name))
    if not matches:
        pytest.skip(f"python-hwpx 동봉 샘플을 찾을 수 없음: {name} (under {_HWPX_PKG_DIR})")
    dest = tmp_path / name
    shutil.copy2(matches[0], dest)
    return dest


@pytest.fixture
def skeleton_hwpx(tmp_path: Path) -> Path:
    """python-hwpx 동봉 빈 골격 문서(Skeleton.hwpx) 사본."""
    return _copy_bundled("Skeleton.hwpx", tmp_path)


@pytest.fixture
def report_table_hwpx(tmp_path: Path) -> Path:
    """python-hwpx 동봉 표 포함 보고서(report_table.hwpx) 사본."""
    return _copy_bundled("report_table.hwpx", tmp_path)


# demo_form_hwpx가 만드는 문서의 내용 (테스트에서 기대값 계산에 사용).
# builder._DEMO_PARAGRAPHS/_DEMO_TABLE_ROWS(placeholders_demo 블록)가 뒤에 붙는다.
DEMO_SPEC = {
    "title": "재난 대응 계획서 (테스트)",
    "paragraphs": ["본 문서는 단위 테스트용 임시 양식이다."],
    "tables": [{"rows": [["구분", "내용"], ["수립 주기", "연 1회"]]}],
    "placeholders_demo": True,
}


@pytest.fixture
def demo_form_hwpx(tmp_path: Path) -> Path:
    """build_hwpx로 생성한 placeholder 포함 임시 양식 hwpx."""
    out = tmp_path / "demo_form.hwpx"
    build_hwpx(DEMO_SPEC, out)
    return out
