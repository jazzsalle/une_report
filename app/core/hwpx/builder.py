"""spec 기반 새 hwpx 문서 생성기 (M1-8).

python-hwpx 라이브러리(HwpxDocument)로 제목·문단·표를 가진 새 hwpx를
만든다. 시나리오 C(신규 문서 생성)와 테스트 픽스처(placeholder 데모
양식) 생성에 쓴다.

spec 형식:
{
    "title": "문서 제목",                # 선택. 첫 문단으로 들어간다
    "paragraphs": ["문단1", "문단2"],    # 선택. 본문 문단 목록
    "tables": [                          # 선택. 표 목록
        {"rows": [["헤더1", "헤더2"], ["값1", "값2"]]}
    ],
    "placeholders_demo": True,           # 선택. True면 placeholder가 포함된
                                         # 데모 양식 블록(문단 4개 + 표 1개)을
                                         # 문서 끝에 덧붙인다 (테스트 픽스처용)
}
- tables[i].rows는 2차원 문자열 배열. 행 길이가 서로 달라도 되며,
  열 수는 가장 긴 행 기준으로 잡고 짧은 행의 나머지 셀은 빈 칸이 된다.
- 모든 값은 str()로 변환해 넣는다.
"""
from pathlib import Path

from hwpx.document import HwpxDocument

# placeholders_demo=True일 때 붙는 데모 양식 (placeholder.PLACEHOLDER_PATTERNS
# 의 bracket/angle/date_stub/filler 유형을 모두 포함)
_DEMO_PARAGRAPHS = (
    "수립 기관: [기관명]",
    "대상 시설: ○○시설",
    "담당자: <담당자 이름>",
    "작성일: YYYY년 MM월 DD일",
)
_DEMO_TABLE_ROWS = (
    ("항목", "내용"),
    ("연락처", "TBD"),
    ("주소", "[주소 입력]"),
)


def _add_table(doc: HwpxDocument, rows: list[list]) -> None:
    """rows(2차원 배열)로 표를 추가하고 각 셀에 텍스트를 채운다."""
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    if n_cols == 0:
        return
    table = doc.add_table(len(rows), n_cols)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).set_text(str(value))


def build_hwpx(spec: dict, output_path: str | Path) -> None:
    """spec(모듈 docstring 참조)대로 새 hwpx를 만들어 output_path에 저장한다."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = HwpxDocument.new()
    try:
        title = spec.get("title")
        if title:
            doc.add_paragraph(str(title))

        for text in spec.get("paragraphs") or []:
            doc.add_paragraph(str(text))

        for tbl_spec in spec.get("tables") or []:
            _add_table(doc, tbl_spec.get("rows") or [])

        if spec.get("placeholders_demo"):
            for text in _DEMO_PARAGRAPHS:
                doc.add_paragraph(text)
            _add_table(doc, [list(r) for r in _DEMO_TABLE_ROWS])

        doc.save_to_path(str(output_path))  # save()는 deprecated
    finally:
        doc.close()
