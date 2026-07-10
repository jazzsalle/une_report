"""UNI RAG 실서버 통합 테스트 (M3 실호출 검증).

기본 pytest 실행에서는 제외되며(`pytest.ini`의 `-m "not integration"`),
`pytest -m integration`으로만 실행한다. `.env`의 TEST_UNE_ACCOUNT /
TEST_UNE_PASSWORD가 없으면 skip한다. 계정 값은 로그·assert 메시지에
노출하지 않는다.
"""
import asyncio

import pytest

from app import config
from app.llm.json_parser import parse_llm_json
from app.llm.uni_rag_client import UniRagClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def token() -> str:
    """실로그인 1회 → JWT 토큰 (모듈 내 테스트가 공유)."""
    if not config.TEST_UNE_ACCOUNT or not config.TEST_UNE_PASSWORD:
        pytest.skip("TEST_UNE_ACCOUNT/TEST_UNE_PASSWORD 미설정 — .env 참조")
    client = UniRagClient()
    return asyncio.run(client.login(config.TEST_UNE_ACCOUNT, config.TEST_UNE_PASSWORD))


def test_real_chat_returns_nonempty_text(token: str):
    """실서버 /chat/ 비스트리밍 호출 → answer 텍스트가 비어있지 않다."""
    client = UniRagClient()
    answer = asyncio.run(
        client.chat("한 문장으로 간단히 자기소개를 해줘.", token=token)
    )
    assert isinstance(answer, str)
    assert answer.strip(), "실서버 answer가 비어 있음"


def test_real_chat_json_instruction_parses(token: str):
    """JSON 지시 프롬프트 → parse_llm_json 복구 성공 + edits 키 존재."""
    client = UniRagClient()
    prompt = (
        "다음 형식의 JSON으로만 답하라. 설명·인사말·코드펜스 외 텍스트를 붙이지 마라.\n"
        '{"edits":[{"id":3,"new_text":"..."}]}\n'
        'id 3 문단을 "본 계획은 매년 갱신한다."로 바꾸는 편집을 만들어라.'
    )
    answer = asyncio.run(client.chat(prompt, token=token))
    parsed = parse_llm_json(answer)
    assert isinstance(parsed, dict)
    assert "edits" in parsed, f"edits 키 없음 — 응답 키: {list(parsed.keys())}"
    assert isinstance(parsed["edits"], list) and parsed["edits"]
    first = parsed["edits"][0]
    assert "id" in first and "new_text" in first
