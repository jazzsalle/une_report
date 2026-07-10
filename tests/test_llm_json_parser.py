"""LLM JSON 복구 파서(M3-4) 단위 테스트.

`parse_llm_json`의 복구 단계(①원문 ②펜스/<think> 제거 ③괄호 구간 추출
④트레일링 콤마·작은따옴표 보정)를 실제 LLM 오염 패턴별로 검증한다.
"""
import pytest

from app.llm.base import LlmJsonParseError
from app.llm.json_parser import parse_llm_json

# 편집 파이프라인의 표준 응답 형태 (DESIGN.md: edits[{id,new_text}])
EDITS_OBJ = {"edits": [{"id": 3, "new_text": "수정된 문장"}]}
EDITS_JSON = '{"edits": [{"id": 3, "new_text": "수정된 문장"}]}'


# ── ① 정상 JSON ────────────────────────────────────────────────

def test_plain_object():
    assert parse_llm_json(EDITS_JSON) == EDITS_OBJ


def test_plain_array():
    assert parse_llm_json('[1, 2, {"a": true}]') == [1, 2, {"a": True}]


def test_surrounding_whitespace():
    assert parse_llm_json(f"\n\n  {EDITS_JSON}  \n") == EDITS_OBJ


# ── ② 코드펜스 / <think> 블록 ──────────────────────────────────

def test_json_code_fence():
    text = f"```json\n{EDITS_JSON}\n```"
    assert parse_llm_json(text) == EDITS_OBJ


def test_bare_code_fence():
    text = f"```\n{EDITS_JSON}\n```"
    assert parse_llm_json(text) == EDITS_OBJ


def test_think_block_before_json():
    text = f"<think>사용자가 3번 문단 수정을 원한다...\n{{중간 괄호}}</think>\n{EDITS_JSON}"
    assert parse_llm_json(text) == EDITS_OBJ


def test_think_block_plus_fence():
    text = f"<THINK>추론 중</THINK>\n```json\n{EDITS_JSON}\n```"
    assert parse_llm_json(text) == EDITS_OBJ


# ── ③ 앞뒤 잡담 속 괄호 구간 추출 ──────────────────────────────

def test_chatter_around_json():
    text = f"물론입니다! 요청하신 편집 내용입니다.\n{EDITS_JSON}\n도움이 되셨길 바랍니다."
    assert parse_llm_json(text) == EDITS_OBJ


def test_chatter_with_fence():
    text = f"다음과 같이 수정하겠습니다:\n```json\n{EDITS_JSON}\n```\n추가 요청 주세요."
    assert parse_llm_json(text) == EDITS_OBJ


def test_braces_inside_string_value():
    # 문자열 값 내부의 { } [ ] 는 괄호 균형 계산에서 무시되어야 한다
    text = '앞말 {"new_text": "예: {항목} 목록은 [a, b]"} 뒷말'
    assert parse_llm_json(text) == {"new_text": "예: {항목} 목록은 [a, b]"}


def test_escaped_quotes_inside_string():
    text = '결과: {"msg": "그는 \\"완료\\"라고 말했다"} 끝'
    assert parse_llm_json(text) == {"msg": '그는 "완료"라고 말했다'}


def test_nested_structures():
    text = '{"a": {"b": [{"c": 1}, {"d": [2, 3]}]}}'
    assert parse_llm_json(text) == {"a": {"b": [{"c": 1}, {"d": [2, 3]}]}}


# ── ④ 트레일링 콤마 / 작은따옴표 보정 ──────────────────────────

def test_trailing_comma_object():
    assert parse_llm_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_trailing_comma_array_multiline():
    text = '{\n  "edits": [\n    {"id": 3, "new_text": "x"},\n  ],\n}'
    assert parse_llm_json(text) == {"edits": [{"id": 3, "new_text": "x"}]}


def test_single_quoted_strings():
    assert parse_llm_json("{'id': 3, 'new_text': '수정'}") == {"id": 3, "new_text": "수정"}


def test_single_quotes_with_inner_double_quote():
    # 작은따옴표 문자열 내부의 " 는 이스케이프되어야 한다
    assert parse_llm_json("{'msg': '그는 \"응\"이라 했다'}") == {"msg": '그는 "응"이라 했다'}


def test_comma_inside_string_not_removed():
    # 문자열 내부의 ",}" 패턴은 트레일링 콤마 제거 대상이 아니다
    assert parse_llm_json('{"a": "쉼표,} 포함", "b": 1,}') == {"a": "쉼표,} 포함", "b": 1}


def test_fence_plus_trailing_comma_combo():
    text = '```json\n{"edits": [{"id": 1, "new_text": "y"},]}\n```'
    assert parse_llm_json(text) == {"edits": [{"id": 1, "new_text": "y"}]}


# ── 복구 불가 → LlmJsonParseError ──────────────────────────────

def test_empty_text_raises():
    with pytest.raises(LlmJsonParseError):
        parse_llm_json("")


def test_whitespace_only_raises():
    with pytest.raises(LlmJsonParseError):
        parse_llm_json("   \n\t ")


def test_non_string_raises():
    with pytest.raises(LlmJsonParseError):
        parse_llm_json(None)  # type: ignore[arg-type]


def test_plain_prose_raises():
    with pytest.raises(LlmJsonParseError):
        parse_llm_json("죄송하지만 해당 요청은 처리할 수 없습니다.")


def test_unbalanced_braces_raises():
    with pytest.raises(LlmJsonParseError):
        parse_llm_json('{"edits": [{"id": 3, "new_text": "잘림')


def test_error_message_contains_snippet():
    with pytest.raises(LlmJsonParseError, match="복구하지 못했습니다"):
        parse_llm_json("완전히 JSON이 아닌 텍스트")
