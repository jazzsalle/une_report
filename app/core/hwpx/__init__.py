"""hwpx 코어(M1) 공개 API.

hwpx(OWPML zip)의 해제·검증·파싱·HTML 변환·부분 편집·재패키징·신규 생성을
제공한다. 외부(서비스·테스트)에서는 이 패키지 루트에서 import한다.
서브모듈 구성: package(zip 계층) → parser(TextNode 추출) → html(미리보기)
/ edits(부분 수정) / placeholder(미채움 검증) / chunker(LLM 배치) / builder(신규 생성).
"""
from .builder import build_hwpx
from .chunker import chunk_nodes
from .edits import EditResult, apply_edits, normalize_edit_id
from .html import HtmlResult, hwpx_to_html
from .models import HWP_UNITS_PER_MM, TextNode, ValidationResult
from .package import (
    extract_hwpx,
    find_header_file,
    find_section_files,
    repack_hwpx,
    validate_hwpx,
)
from .parser import collect_runs_and_texts, parse_section
from .styles import guide_char_pr_ids
from .placeholder import (
    PLACEHOLDER_PATTERNS,
    PlaceholderHit,
    PlaceholderReport,
    collect_placeholders,
    verify_output,
)

__all__ = [
    # package (zip 계층)
    "extract_hwpx",
    "find_header_file",
    "find_section_files",
    "repack_hwpx",
    "validate_hwpx",
    # parser
    "parse_section",
    "collect_runs_and_texts",
    # styles
    "guide_char_pr_ids",
    # html
    "hwpx_to_html",
    "HtmlResult",
    # edits
    "apply_edits",
    "normalize_edit_id",
    "EditResult",
    # placeholder
    "PLACEHOLDER_PATTERNS",
    "PlaceholderHit",
    "PlaceholderReport",
    "collect_placeholders",
    "verify_output",
    # chunker
    "chunk_nodes",
    # builder
    "build_hwpx",
    # models
    "TextNode",
    "ValidationResult",
    "HWP_UNITS_PER_MM",
]
