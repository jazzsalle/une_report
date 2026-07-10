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
