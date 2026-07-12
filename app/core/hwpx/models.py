"""hwpx 코어(M1) 데이터 모델."""
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# HWPUNIT 환산 계수: 1인치 = 7200 HWPUNIT, 1인치 = 25.4mm
HWP_UNITS_PER_MM: float = 7200 / 25.4


@dataclass
class TextNode:
    """섹션 XML에서 추출한 편집 가능 텍스트 단위(문단 또는 표 셀).

    - id: 섹션 내 0부터 시작하는 순번. 순번 규칙은 parser.parse_section docstring 참조.
    - type: "body_text"(본문 문단) | "table_cell"(표 셀)
    - text: 앞뒤 공백을 strip한 텍스트
    - raw_text: strip하지 않은 원문 텍스트(들여쓰기 보존)
    - table_idx/row/col: 표 셀일 때만 유효(본문이면 -1)
    - t_elems: 이 노드에 속한 hp:t ET Element 참조 목록.
      apply_edits 단계에서 이 참조를 통해 원본 트리를 부분 수정한다.
    - elem: 노드의 컨테이너 요소 참조(table_cell이면 hp:tc, body_text면 hp:p).
      t_elems가 비어 있는 빈 노드에 hp:t를 생성 삽입할 때 앵커로 쓴다.
    """

    id: int
    type: str
    text: str
    raw_text: str
    table_idx: int = -1
    row: int = -1
    col: int = -1
    cell_col_span: int = 1
    cell_row_span: int = 1
    cell_width_mm: int = 0
    cell_height_mm: int = 0
    t_elems: list[ET.Element] = field(default_factory=list)
    elem: ET.Element | None = None


@dataclass
class ValidationResult:
    """hwpx 패키지 구조 검증 결과."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
