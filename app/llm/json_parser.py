"""LLM 출력 JSON 복구 파서 (M3-4).

LLM이 반환한 텍스트에서 JSON을 최대한 복구해 파싱한다. 복구 단계는
process-gpt-completion의 CustomJsonOutputParser 방식을 응용했다:

  ① 원문 그대로 json.loads
  ② 마크다운 코드펜스(```json ... ```)와 <think>...</think> 블록 제거 후
  ③ 첫 `{`/`[`부터 짝이 맞는 닫힘 괄호까지 추출 후 (문자열 리터럴 인식)
  ④ 트레일링 콤마 제거 + 작은따옴표 문자열 보정 후

각 단계에서 파싱에 성공하면 즉시 반환하고, 모두 실패하면
`LlmJsonParseError`를 던진다.
"""
import json
import re
from typing import Any

from app.llm.base import LlmJsonParseError

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """<think>...</think> 블록을 제거한다 (thinking 모델 대비)."""
    return _THINK_RE.sub("", text)


def _strip_code_fences(text: str) -> str:
    """첫 번째 마크다운 코드펜스의 내용을 꺼낸다. 펜스가 없으면 원문 유지."""
    m = _FENCE_RE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return text.strip()


def _extract_balanced(text: str) -> str | None:
    """첫 `{` 또는 `[`부터 짝이 맞는 닫힘까지 추출한다.

    큰따옴표 문자열 리터럴 내부의 괄호와 이스케이프(\\")는 건너뛴다.
    짝이 맞지 않으면 None.
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)
    stack: list[str] = []
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or (stack.pop(), ch) not in (("{", "}"), ("[", "]")):
                return None
            if not stack:
                return text[start:i + 1]
    return None


def _fix_single_quotes(text: str) -> str:
    """작은따옴표 문자열 리터럴을 큰따옴표로 보정한다.

    큰따옴표 문자열 내부는 건드리지 않으며, 변환 시 내부의 `"`는 이스케이프하고
    `\\'`는 `'`로 되돌린다. 닫히지 않은 작은따옴표는 그대로 둔다.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_double = False
    escape = False
    while i < n:
        ch = text[i]
        if in_double:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            buf: list[str] = []
            j = i + 1
            closed = False
            while j < n:
                c = text[j]
                if c == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append("'" if nxt == "'" else c + nxt)
                    j += 2
                    continue
                if c == "'":
                    closed = True
                    break
                buf.append('\\"' if c == '"' else c)
                j += 1
            if closed:
                out.append('"' + "".join(buf) + '"')
                i = j + 1
            else:
                out.append(ch)
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _remove_trailing_commas(text: str) -> str:
    """`,` 뒤에 공백을 사이에 두고 `}` 또는 `]`가 오면 콤마를 제거한다.

    큰따옴표 문자열 리터럴 내부의 콤마는 건드리지 않는다.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # 트레일링 콤마 삭제
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _complete_truncated(text: str) -> str | None:
    """잘린 JSON을 '마지막 안전 지점'까지 잘라 닫는 괄호를 붙여 완성한다.

    max_tokens 상한으로 응답이 중간에 끊긴 경우의 부분 복구용.
    _extract_balanced와 같은 상태기계(문자열 리터럴·이스케이프 인식)로
    스캔하며 아래 세 곳만 '안전 지점'으로 기록한다:

    1. 원문에 **명시적으로 닫힌** 하위 트리(`}`/`]`) 직후 — 진짜 완결된 값
    2. `[`가 막 열린 직후 — 빈 배열로 닫아도 항목을 날조하지 않음
    3. 최상위 컨테이너(depth 1)의 스칼라 값 완결 직후 — reply 등 헤더 필드

    쓰다 만 중첩 객체를 조기에 닫아 부분 항목({"id":2}처럼 new_text 없는
    edit)을 날조하는 것을 막기 위해, 깊은 곳의 스칼라 완결·`{` 직후는
    안전 지점으로 삼지 않는다. 키 문자열·콜론·미완성 토큰도 마찬가지.

    EOF에서 괄호 짝이 안 맞으면 마지막 안전 지점에서 자르고 그 시점의
    열린 괄호를 역순으로 닫아 반환한다. 안전 지점이 없거나 원문이
    균형이면(잘림 아님) None.
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)

    # 스택 원소: ["{", 상태] — 객체 상태: key→colon→value→post, 배열: value→post
    stack: list[list[str]] = []
    in_string = False
    escape = False
    string_is_value = False
    in_token = False  # 숫자·true/false/null 토큰 스캔 중
    last_safe: tuple[int, list[str]] | None = None  # (포함 끝 인덱스, 괄호 스냅샷)

    def _mark_safe(i: int) -> None:
        nonlocal last_safe
        last_safe = (i, [entry[0] for entry in stack])

    def _end_token(i_prev: int) -> None:
        """토큰이 i_prev에서 끝났다 — 최상위 스칼라만 안전 지점(규칙 3)."""
        nonlocal in_token
        in_token = False
        if stack:
            stack[-1][1] = "post"
            if len(stack) == 1:
                _mark_safe(i_prev)

    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                if stack and string_is_value:
                    stack[-1][1] = "post"
                    if len(stack) == 1:
                        _mark_safe(i)  # 규칙 3: 최상위 스칼라 완결
                elif stack and stack[-1][0] == "{" and stack[-1][1] == "key":
                    stack[-1][1] = "colon"
            i += 1
            continue
        if in_token and (ch in ",}]" or ch in " \t\r\n"):
            _end_token(i - 1)
            continue  # 같은 문자를 아래 로직으로 재처리
        if ch == '"':
            in_string = True
            string_is_value = not (stack and stack[-1][0] == "{" and stack[-1][1] == "key")
        elif ch == "{" or ch == "[":
            stack.append([ch, "key" if ch == "{" else "value"])
            if ch == "[":
                _mark_safe(i)  # 규칙 2: 빈 배열로 닫아도 항목 날조 없음
        elif ch == "}" or ch == "]":
            if not stack or (stack[-1][0], ch) not in (("{", "}"), ("[", "]")):
                return None  # 구조 자체가 깨짐 — 잘림 복구 범위 밖
            stack.pop()
            if not stack:
                return None  # 균형 도달 = 잘린 게 아님 (다른 원인)
            stack[-1][1] = "post"
            _mark_safe(i)  # 규칙 1: 명시적으로 닫힌 하위 트리
        elif ch == ":":
            if stack and stack[-1][0] == "{" and stack[-1][1] == "colon":
                stack[-1][1] = "value"
        elif ch == ",":
            if stack:
                stack[-1][1] = "key" if stack[-1][0] == "{" else "value"
        elif ch not in " \t\r\n":
            # 숫자·true/false/null 토큰 시작 (값 자리 여부는 관대하게 취급)
            in_token = True
        i += 1

    # EOF에서 끊긴 토큰("12…")은 완결 보장이 없으므로 채택하지 않는다
    # (last_safe는 그 토큰 이전 안전 지점을 가리킨다)
    if not stack or last_safe is None:
        return None
    cut, snapshot = last_safe
    closers = "".join("}" if b == "{" else "]" for b in reversed(snapshot))
    return text[start:cut + 1] + closers


def parse_llm_json_or_partial(text: str) -> tuple[Any, bool]:
    """(파싱 결과, truncated 여부)를 반환한다.

    완성 JSON이면 (obj, False). parse_llm_json이 실패하면 정리본
    (<think>·펜스 제거)에 잘림 복구(_complete_truncated)를 시도하고,
    성공 시 (obj, True) — 호출부는 truncated=True일 때 "응답이 잘려
    일부만 반영" 안내를 덧붙여야 한다. 복구도 실패하면 LlmJsonParseError.
    """
    try:
        return parse_llm_json(text), False
    except LlmJsonParseError:
        pass
    repaired = None
    if isinstance(text, str):
        cleaned = _strip_code_fences(_strip_think_blocks(text)).strip()
        repaired = _complete_truncated(cleaned)
    if repaired is not None:
        try:
            return json.loads(repaired), True
        except json.JSONDecodeError:
            # 잘림 + 작은따옴표 등 복합 훼손 — 기존 보정기를 한 번 더
            fixed = _remove_trailing_commas(_fix_single_quotes(repaired))
            try:
                return json.loads(fixed), True
            except json.JSONDecodeError:
                pass
    snippet = (text or "").strip()[:200]
    raise LlmJsonParseError(f"LLM 응답을 JSON으로 복구하지 못했습니다(잘림 복구 포함): {snippet!r}")


def parse_llm_json(text: str) -> Any:
    """LLM 응답 텍스트에서 JSON을 복구·파싱해 반환한다.

    복구 단계(①~④)는 모듈 docstring 참조. 모든 단계가 실패하면
    원본 텍스트 앞부분을 포함한 `LlmJsonParseError`를 던진다.
    """
    if not isinstance(text, str) or not text.strip():
        raise LlmJsonParseError("LLM 응답이 비어 있어 JSON을 파싱할 수 없습니다")

    # ① 원문 그대로
    candidates = [text]
    # ② <think> 블록·코드펜스 제거
    cleaned = _strip_code_fences(_strip_think_blocks(text))
    candidates.append(cleaned)
    # ③ 짝 맞는 괄호 구간 추출
    extracted = _extract_balanced(cleaned)
    if extracted:
        candidates.append(extracted)

    tried: list[str] = []
    for cand in candidates:
        if cand in tried:
            continue
        tried.append(cand)
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass

    # ④ 작은따옴표·트레일링 콤마 보정 (추출본 우선, 실패 시 정리본)
    for cand in ([extracted] if extracted else []) + [cleaned]:
        repaired = _remove_trailing_commas(_fix_single_quotes(cand))
        if repaired in tried:
            continue
        tried.append(repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        # 보정으로 괄호 구간이 새로 드러났을 수 있으므로 한 번 더 추출 시도
        re_extracted = _extract_balanced(repaired)
        if re_extracted and re_extracted not in tried:
            tried.append(re_extracted)
            try:
                return json.loads(re_extracted)
            except json.JSONDecodeError:
                pass

    snippet = text.strip()[:200]
    raise LlmJsonParseError(f"LLM 응답을 JSON으로 복구하지 못했습니다: {snippet!r}")
