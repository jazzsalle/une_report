"""공문서 항목 번호·기호 체계 감지 및 프롬프트 규칙 생성.

「행정업무의 운영 및 혁신에 관한 규정」의 문서 작성 방식:
내용을 둘 이상의 항목으로 구분할 때 상위→하위 순서로
1., 가., 1), 가), (1), (가), ①, ㉮ 형태로 표시하고(숫자는 오름차순,
한글은 가나다순), 필요하면 □, ○, -, ㆍ 같은 특수 기호를 쓸 수 있다.

이 모듈은 템플릿 노드 텍스트에서 이미 쓰이고 있는 항목 기호를 감지해
(일부만 있어도) LLM이 그 체계를 이어받아 새 내용 전체에 일관되게
부여하도록 하는 프롬프트 규칙 텍스트를 만든다 — 템플릿에 기호가 일부만
있다고 본문 텍스트만 나열하는 결과를 방지한다(2026-07-13 사용자 요구).
"""
import re

# 표준 항목 표시 순서 (상위 → 하위)
STANDARD_LEVELS = ["1.", "가.", "1)", "가)", "(1)", "(가)", "①", "㉮"]
# 허용되는 특수 기호 (규정 예시)
SPECIAL_SYMBOLS = ["□", "○", "-", "ㆍ"]

# 가나다순 항목에 쓰이는 한글 낱자
_GANADA = "가나다라마바사아자차카타파하"

# (기호 이름, 노드 텍스트 선두 매칭 정규식) — 위에서부터 먼저 맞는 것 하나만 채택.
# 숫자는 1~2자리로 제한해 연도("2026.")·소수("1.5")류 오탐을 줄인다.
_MARKER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("(1)", re.compile(r"^\(\d{1,2}\)")),
    ("(가)", re.compile(rf"^\([{_GANADA}]\)")),
    ("1.", re.compile(r"^\d{1,2}\.(?=\s|$)")),
    ("가.", re.compile(rf"^[{_GANADA}]\.(?=\s|$)")),
    ("1)", re.compile(r"^\d{1,2}\)")),
    ("가)", re.compile(rf"^[{_GANADA}]\)")),
    ("①", re.compile(r"^[①-⑳]")),          # ①~⑳
    ("㉮", re.compile(r"^[㉠-㉻]")),          # ㉠~㉻ (공문서 표준은 ㉮류)
    ("□", re.compile(r"^[□■◇◆]")),
    ("○", re.compile(r"^[○●◎◦ㅇ]")),               # 실무상 이응(ㅇ)을 ○ 대용으로 흔히 씀
    ("-", re.compile(r"^[-–—](?=\s)")),               # 음수·괄호식 오탐 방지: 뒤에 공백 필수
    ("ㆍ", re.compile(r"^[ㆍ·•]")),
]


def detect_item_scheme(nodes: list[dict]) -> list[str]:
    """노드 텍스트 선두의 항목 기호를 감지해 첫 등장 순서로 반환한다.

    공문서는 상위 항목이 먼저 나오므로 첫 등장 순서가 곧 상위→하위
    체계의 근사가 된다. 같은 기호는 한 번만 담는다.
    """
    seen: list[str] = []
    for n in nodes:
        text = (n.get("text") or "").strip()
        if not text:
            continue
        for name, pattern in _MARKER_PATTERNS:
            if pattern.match(text):
                if name not in seen:
                    seen.append(name)
                break
    return seen


def build_numbering_rule(scheme: list[str]) -> str:
    """프롬프트 '규칙' 목록에 끼워 넣을 항목 부여 규칙 한 덩어리를 만든다.

    edit·fill 공용: 새로 작성하는 내용에는 체계를 부여하고,
    기존 문장 일부 수정에는 원문 기호를 유지하게 한다.
    """
    standard = " → ".join(STANDARD_LEVELS)
    specials = ", ".join(SPECIAL_SYMBOLS)
    if scheme:
        detected = " → ".join(scheme)
        scheme_line = (
            f"이 문서는 항목 기호 체계 {detected} (상위→하위, 등장 순)를 이미 "
            "사용 중이다. 새로 작성하는 내용도 이 체계를 이어받아 부여하고, "
            f"더 깊은 하위 항목이 필요하면 표준 순서({standard})에서 이어 써라."
        )
    else:
        scheme_line = (
            f"이 문서에서 감지된 항목 기호가 없으므로 표준 순서({standard})를 "
            "상위 항목부터 적용하라."
        )
    return (
        "- 항목 번호·기호 부여(공문서 작성 방식): 내용을 둘 이상의 항목으로 "
        "구분할 때는 문장이 시작될 때마다 그 항목의 소속 수준(종속 관계)을 "
        f"판단해 상위→하위 순서로 기호를 부여하라. 숫자 항목은 오름차순, 한글 "
        f"항목은 가나다순이며, 필요하면 {specials} 같은 특수 기호를 쓸 수 있다. "
        f"{scheme_line} 같은 수준의 항목에는 같은 기호를, 하위 내용에는 다음 "
        "수준의 기호를 붙여 목차부터 본문까지 완성된 보고서 형태로 작성하라. "
        "항목 기호 없이 본문 텍스트만 나열하지 마라. 단, 기존 문장의 일부만 "
        "고치는 경우에는 원문의 항목 기호를 그대로 유지하라."
    )
