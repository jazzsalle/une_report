"""UniRagClient(M3-2/3/5) 단위 테스트 — httpx.MockTransport 주입, 네트워크 불필요.

검증 항목:
- login 성공(access_token/token 키)·실패(401 → UniRagAuthError)
- chat 정상 응답 → answer 반환 + 요청 바디(model_key/stream/thinking/history/top_k)·
  Authorization 헤더 검증
- 에러 매핑: 401/403 → UniRagAuthError(=LlmAuthError), 5xx → LlmUnavailableError,
  ConnectError → LlmUnavailableError, ReadTimeout → LlmTimeoutError,
  answer 누락·비JSON → LlmError
- chat_stream: SSE 델타 결합, [DONE] 종료, __sources__ 보관(답변 미포함)
"""
import asyncio
import json

import httpx
import pytest

from app.llm.base import (
    LlmAuthError,
    LlmError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.llm.uni_rag_client import UniRagAuthError, UniRagClient

BASE = "http://uni-rag.test"


def _client_with(handler) -> UniRagClient:
    """MockTransport를 주입한 UniRagClient를 만든다."""
    return UniRagClient(base_url=BASE, transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


# ── login ──────────────────────────────────────────────────────

def test_login_success_access_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/login"
        assert json.loads(request.content) == {"account": "user", "password": "pw"}
        return httpx.Response(200, json={"access_token": "tok-123"})

    token = _run(_client_with(handler).login("user", "pw"))
    assert token == "tok-123"


def test_login_success_token_key_fallback():
    def handler(request):
        return httpx.Response(200, json={"token": "tok-alt"})

    assert _run(_client_with(handler).login("u", "p")) == "tok-alt"


def test_login_401_raises_auth_error():
    def handler(request):
        return httpx.Response(401, json={"detail": "invalid"})

    with pytest.raises(UniRagAuthError):
        _run(_client_with(handler).login("u", "wrong"))


def test_login_response_without_token_raises():
    def handler(request):
        return httpx.Response(200, json={"detail": "ok"})

    with pytest.raises(LlmError):
        _run(_client_with(handler).login("u", "p"))


# ── chat (stream=false) ────────────────────────────────────────

def test_chat_returns_answer_and_sends_expected_request():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answer": "안녕하세요", "sources": []})

    history = [{"role": "user", "content": "이전 질문"}]
    answer = _run(
        _client_with(handler).chat("질문입니다", history, token="tok-123")
    )

    assert answer == "안녕하세요"
    assert captured["path"] == "/chat/"
    assert captured["auth"] == "Bearer tok-123"
    body = captured["body"]
    assert body["query"] == "질문입니다"
    assert body["history"] == history
    assert body["model_key"]  # config 기본값(qwen3-coder-next 등)이 채워진다
    assert body["stream"] is False
    assert body["thinking"] is False
    assert body["top_k"] == 5


def test_chat_model_key_and_opts_override():
    captured: dict = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answer": "ok"})

    _run(
        _client_with(handler).chat(
            "q", token="t", model_key="other-model", top_k=3, thinking=True
        )
    )
    body = captured["body"]
    assert body["model_key"] == "other-model"
    assert body["top_k"] == 3
    assert body["thinking"] is True  # **opts가 기본값을 덮어쓴다


@pytest.mark.parametrize("status", [401, 403])
def test_chat_auth_error(status):
    def handler(request):
        return httpx.Response(status)

    with pytest.raises(UniRagAuthError):
        _run(_client_with(handler).chat("q", token="expired"))

    # 상위 계층(LlmAuthError)으로도 포착 가능해야 한다 (M3-6 교체 가능성)
    with pytest.raises(LlmAuthError):
        _run(_client_with(handler).chat("q", token="expired"))


def test_chat_5xx_raises_unavailable():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(LlmUnavailableError, match="503"):
        _run(_client_with(handler).chat("q", token="t"))


def test_chat_connect_error_raises_unavailable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LlmUnavailableError):
        _run(_client_with(handler).chat("q", token="t"))


def test_chat_read_timeout_raises_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(LlmTimeoutError):
        _run(_client_with(handler).chat("q", token="t"))


def test_chat_missing_answer_raises_llm_error():
    def handler(request):
        return httpx.Response(200, json={"sources": []})

    with pytest.raises(LlmError, match="answer"):
        _run(_client_with(handler).chat("q", token="t"))


def test_chat_non_json_body_raises_llm_error():
    def handler(request):
        return httpx.Response(200, text="<html>gateway error</html>")

    with pytest.raises(LlmError):
        _run(_client_with(handler).chat("q", token="t"))


# ── chat_stream (SSE) ──────────────────────────────────────────

def _sse(*payloads: str) -> str:
    """data: 라인들로 SSE 본문을 만든다."""
    return "".join(f"data: {p}\n\n" for p in payloads)


def test_chat_stream_joins_deltas_and_stops_at_done():
    body = _sse(
        json.dumps("안녕"),
        json.dumps(" 하세요"),
        json.dumps("."),
        "[DONE]",
        json.dumps("DONE 이후는 무시"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, text=body, headers={"Content-Type": "text/event-stream"}
        )

    async def collect():
        client = _client_with(handler)
        chunks = [c async for c in client.chat_stream("q", token="t")]
        return chunks

    assert "".join(_run(collect())) == "안녕 하세요."


def test_chat_stream_keeps_sources_out_of_answer():
    sources = [{"source": "doc.pdf", "score": 0.9}]
    body = _sse(
        json.dumps("답변"),
        json.dumps({"__sources__": sources}, ensure_ascii=False),
        json.dumps(" 텍스트"),
        "[DONE]",
    )

    def handler(request):
        return httpx.Response(200, text=body)

    async def collect():
        client = _client_with(handler)
        chunks = [c async for c in client.chat_stream("q", token="t")]
        return client, chunks

    client, chunks = _run(collect())
    assert "".join(chunks) == "답변 텍스트"  # __sources__는 답변에 미포함
    assert client.last_sources == sources


def test_chat_stream_delta_with_escapes():
    # json.loads가 \n·\" 이스케이프를 풀어서 yield해야 한다
    body = _sse(json.dumps('줄\n"바꿈"'), "[DONE]")

    def handler(request):
        return httpx.Response(200, text=body)

    async def collect():
        return [c async for c in _client_with(handler).chat_stream("q", token="t")]

    assert _run(collect()) == ['줄\n"바꿈"']


def test_chat_stream_auth_error():
    def handler(request):
        return httpx.Response(401)

    async def consume():
        async for _ in _client_with(handler).chat_stream("q", token="expired"):
            pass

    with pytest.raises(UniRagAuthError):
        _run(consume())


def test_chat_stream_connect_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    async def consume():
        async for _ in _client_with(handler).chat_stream("q", token="t"):
            pass

    with pytest.raises(LlmUnavailableError):
        _run(consume())
