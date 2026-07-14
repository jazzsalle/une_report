"""T3Q 클라이언트 단위 테스트 — httpx.MockTransport, 네트워크 0회.

응답 픽스처는 실서버 실측(2026-07-13 RPT-002)과 API 명세서
(upgrade/(251124)MOIS_API_명세서_v0.8.5.xlsx) 예시를 따른다.
"""
import asyncio
import json

import httpx
import pytest

from app.llm.base import LlmTimeoutError, LlmUnavailableError
from app.llm.t3q_client import T3qChatBackend, T3qError, T3qReportClient

CRITERIA = {
    "subject": "코로나19 재유행 대비계획",
    "backgroundInfo": {"disasterType": "감염병", "controlPhase": "대비"},
    "purposeOfDocument": {
        "goalOfBusiness": "재난안전계획서 작성",
        "role": "재난안전계획 수립 담당자",
        "targetAudiences": ["중앙정부", "지자체"],
    },
}

TOC_RESPONSE = {
    "title": "코로나19 재유행 대비계획서",
    "sections": [
        {"name": "1. 개요", "children": [
            {"name": "1.1. 목적", "children": []},
            {"name": "1.2. 추진 배경", "children": []},
        ]},
        {"name": "2. 대응 체계", "children": []},
    ],
}


def _run(coro):
    return asyncio.run(coro)


def _report_client(handler) -> T3qReportClient:
    return T3qReportClient(
        base_url="https://t3q.test", transport=httpx.MockTransport(handler)
    )


def _chat_backend(handler) -> T3qChatBackend:
    return T3qChatBackend(
        base_url="https://t3q.test", transport=httpx.MockTransport(handler)
    )


# ── API-RPT-001 목차 ────────────────────────────────────────────

class TestGenerateToc:
    def test_success_wraps_criteria_in_data(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=TOC_RESPONSE)

        result = _run(_report_client(handler).generate_toc(CRITERIA))
        assert captured["path"] == "/model-api/ae894/reports/plan/toc"
        assert captured["body"] == {"data": CRITERIA}  # 기준정보는 data로 감싼다
        assert result["title"] == "코로나19 재유행 대비계획서"
        assert len(result["sections"]) == 2

    def test_422_raises_t3q_error_with_detail(self):
        def handler(_r):
            return httpx.Response(422, json={"detail": [{"msg": "Field required"}]})

        with pytest.raises(T3qError, match="요청 형식 오류"):
            _run(_report_client(handler).generate_toc({}))

    def test_5xx_raises_unavailable(self):
        def handler(_r):
            return httpx.Response(503, text="down")

        with pytest.raises(LlmUnavailableError):
            _run(_report_client(handler).generate_toc(CRITERIA))

    def test_connect_error_raises_unavailable(self):
        def handler(_r):
            raise httpx.ConnectError("no route")

        with pytest.raises(LlmUnavailableError):
            _run(_report_client(handler).generate_toc(CRITERIA))

    def test_timeout_raises_timeout(self):
        def handler(_r):
            raise httpx.ReadTimeout("slow")

        with pytest.raises(LlmTimeoutError):
            _run(_report_client(handler).generate_toc(CRITERIA))

    def test_missing_sections_raises(self):
        def handler(_r):
            return httpx.Response(200, json={"title": "제목만"})

        with pytest.raises(T3qError, match="sections"):
            _run(_report_client(handler).generate_toc(CRITERIA))


# ── API-RPT-002 본문 스트리밍 ───────────────────────────────────

def _sse(*payloads: str) -> bytes:
    return "".join(f"data: {p}\n\n" for p in payloads).encode("utf-8")


CONTENT_SSE = _sse(
    json.dumps({"name": "1.1. 목적", "content": "본 계획은 …", "references": [
        {"id": "chunk_101", "fileId": "f1", "fileName": "감염병예방법.pdf", "page": "4"}
    ]}, ensure_ascii=False),
    json.dumps({"name": "1.2. 추진 배경", "content": "2024년 …", "references": []},
               ensure_ascii=False),
    "[DONE]",
)


async def _collect_content(client: T3qReportClient, sections) -> list[dict]:
    return [item async for item in client.generate_content(CRITERIA, sections)]


class TestGenerateContent:
    def test_stream_yields_sections_in_order(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, content=CONTENT_SSE,
                headers={"content-type": "text/event-stream"},
            )

        items = _run(_collect_content(_report_client(handler), TOC_RESPONSE["sections"]))
        assert [i["name"] for i in items] == ["1.1. 목적", "1.2. 추진 배경"]
        assert items[0]["references"][0]["fileName"] == "감염병예방법.pdf"
        # 요청: 기준정보 + sections + stream=true가 data로 감싸진다
        data = captured["body"]["data"]
        assert data["stream"] is True
        assert data["sections"] == TOC_RESPONSE["sections"]
        assert data["subject"] == CRITERIA["subject"]

    def test_section_error_line_yielded_not_raised(self):
        sse = _sse(
            '{"name": "1.1. 목적", "content": "정상", "references": []}',
            '{"name": "1.2. 추진 배경", "requestId": "req-1", "error": "생성 실패"}',
            "[DONE]",
        )

        def handler(_r):
            return httpx.Response(200, content=sse)

        items = _run(_collect_content(_report_client(handler), []))
        assert items[0]["content"] == "정상"
        assert items[1]["error"] == "생성 실패"
        assert items[1]["requestId"] == "req-1"

    def test_missing_done_raises_aborted(self):
        sse = _sse('{"name": "1.1. 목적", "content": "일부만", "references": []}')

        def handler(_r):
            return httpx.Response(200, content=sse)

        client = _report_client(handler)

        async def _consume():
            got = []
            with pytest.raises(T3qError, match="중단"):
                async for item in client.generate_content(CRITERIA, []):
                    got.append(item)
            return got

        got = _run(_consume())
        assert got and got[0]["content"] == "일부만"  # 받은 섹션은 소비됨

    def test_http_error_status_raises(self):
        def handler(_r):
            return httpx.Response(500, text="oops")

        client = _report_client(handler)
        with pytest.raises(LlmUnavailableError):
            _run(_collect_content(client, []))


# ── API-LLM-001 편집 모드 백엔드 ────────────────────────────────

def _openai_response(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "chat-1", "object": "chat.completion", "created": 0, "model": "mois",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class TestT3qChatBackend:
    def test_chat_returns_content_and_finish_reason(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_openai_response("답변입니다", "length"))

        backend = _chat_backend(handler)
        result = _run(backend.chat(
            "질문", [{"role": "user", "content": "이전"}], token="ignored-token",
        ))
        assert result == "답변입니다"
        assert backend.last_finish_reason == "length"  # 잘림 신호 보존
        body = captured["body"]
        assert captured["path"] == "/llms/v1/chat/completions"
        assert body["model"] == "mois"
        assert body["messages"] == [
            {"role": "user", "content": "이전"},
            {"role": "user", "content": "질문"},
        ]
        assert "token" not in body  # 계약 외 인자는 요청에 싣지 않는다
        assert body["stream"] is False

    def test_chat_stream_yields_deltas(self):
        sse = _sse(
            '{"id":"c","choices":[{"delta":{"content":"태풍"},"index":0}]}',
            '{"id":"c","choices":[{"delta":{"content":" 대비"},"index":0}]}',
            '{"id":"c","choices":[{"delta":{},"finish_reason":"stop","index":0}]}',
            "[DONE]",
        )

        def handler(_r):
            return httpx.Response(200, content=sse)

        backend = _chat_backend(handler)

        async def _consume():
            return [d async for d in backend.chat_stream("질문", token="x")]

        deltas = _run(_consume())
        assert "".join(deltas) == "태풍 대비"
        assert backend.last_finish_reason == "stop"

    def test_chat_malformed_response_raises(self):
        def handler(_r):
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(T3qError, match="형식"):
            _run(_chat_backend(handler).chat("질문"))

    def test_chat_5xx_unavailable(self):
        def handler(_r):
            return httpx.Response(502, text="bad gateway")

        with pytest.raises(LlmUnavailableError):
            _run(_chat_backend(handler).chat("질문"))
