"""T3Q 플랫폼 API 클라이언트 (docs/t3q_upgrade_design.md §2·§3).

세 API를 감싼다 (명세: upgrade/(251124)MOIS_API_명세서_v0.8.5.xlsx):

- API-RPT-001 목차 자동생성  POST /model-api/ae894/reports/plan/toc
  요청 {"data": 기준정보} → 응답 {"title": str, "sections": [{"name","children"} 재귀]}
- API-RPT-002 본문 자동생성  POST /model-api/ae894/reports/plan/content
  요청 {"data": 기준정보+sections+stream:true} → SSE:
  리프 섹션당 `data: {"name","content","references"}`,
  오류 `data: {"name","requestId","error"}`, 종료 `data: [DONE]`
  (2026-07-13 실서버 실측과 일치)
- API-LLM-001 LLM 텍스트 생성  POST /llms/v1/chat/completions
  OpenAI Chat Completions 호환 (model="mois" 고정) — 기존 hwpx 편집 모드의
  LLMBackend 구현체(T3qChatBackend)로 사용. finish_reason=="length"가
  잘림 신호로 온다 (last_finish_reason에 보관).

TLS: config.T3Q_TLS_VERIFY / T3Q_CA_PATH. 실측(2026-07-14) cadm-ca.crt는
UNE 자체 CA라 T3Q 서버 체인과 불일치 — 우회 설정은 config 주석 참조.

에러 매핑은 UniRagClient와 동일 계열: 타임아웃→LlmTimeoutError,
연결 불가·5xx→LlmUnavailableError, 그 외 비정상 상태→LlmError(T3qError).
"""
import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from app import config
from app.llm.base import (
    LLMBackend,
    LlmError,
    LlmTimeoutError,
    LlmUnavailableError,
)


class T3qError(LlmError):
    """T3Q API 호출 실패 (LlmError 계열)."""


def _tls_verify() -> bool | str:
    """httpx verify 옵션 — CA 파일이 있으면 경로, 없으면 시스템 기본.

    T3Q_TLS_VERIFY=false면 검증 우회 (인증서 체인 불일치 임시 대응).
    """
    if not config.T3Q_TLS_VERIFY:
        return False
    ca = Path(config.T3Q_CA_PATH)
    return str(ca) if ca.is_file() else True


def _raise_for_status(r: httpx.Response, api_label: str) -> None:
    if r.status_code == 200:
        return
    if r.status_code >= 500:
        raise LlmUnavailableError(f"T3Q 서버 오류 ({api_label}, HTTP {r.status_code})")
    if r.status_code == 422:
        raise T3qError(f"T3Q 요청 형식 오류 ({api_label}): {r.text[:300]}")
    raise T3qError(f"T3Q 호출 실패 ({api_label}, HTTP {r.status_code}): {r.text[:200]}")


class T3qReportClient:
    """API-RPT-001/002 래퍼 — 재난안전계획서 목차·본문 생성."""

    TOC_PATH = "/model-api/ae894/reports/plan/toc"
    CONTENT_PATH = "/model-api/ae894/reports/plan/content"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or config.T3Q_BASE_URL).rstrip("/")
        self.timeout = timeout or config.T3Q_TIMEOUT
        # 테스트 주입 지점 (httpx.MockTransport — 네트워크 0회 검증)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self._transport, verify=_tls_verify()
        )

    async def generate_toc(self, criteria: dict) -> dict:
        """API-RPT-001: 기준정보 → {"title", "sections"} 목차 트리."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self.base_url}{self.TOC_PATH}", json={"data": criteria}
                )
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"T3Q 목차 생성 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"T3Q 서버에 연결할 수 없습니다: {e}") from e
        _raise_for_status(r, "API-RPT-001")
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise T3qError(f"목차 응답이 JSON이 아닙니다: {r.text[:200]!r}") from e
        if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise T3qError(f"목차 응답에 sections가 없습니다: {keys}")
        return {"title": data.get("title") or "", "sections": data["sections"]}

    async def generate_content(
        self, criteria: dict, sections: list[dict]
    ) -> AsyncIterator[dict]:
        """API-RPT-002 (stream=true): 리프 섹션 결과를 도착 순서대로 yield.

        yield 형태:
        - 정상: {"name", "content", "references"}
        - 섹션 오류: {"name", "error", "requestId"} — 예외로 만들지 않고
          항목으로 넘긴다 (다른 섹션 결과 보존, 호출부가 상태 표시)
        `[DONE]` 수신 없이 스트림이 끝나면 T3qError (중단으로 간주).
        """
        body = {"data": {**criteria, "sections": sections, "stream": True}}
        done = False
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", f"{self.base_url}{self.CONTENT_PATH}", json=body
                ) as r:
                    if r.status_code != 200:
                        await r.aread()
                        _raise_for_status(r, "API-RPT-002")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if not payload:
                            continue
                        if payload == "[DONE]":
                            done = True
                            break
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue  # 비JSON 라인은 무시 (방어적)
                        if not isinstance(obj, dict) or "name" not in obj:
                            continue
                        if "error" in obj:
                            yield {
                                "name": obj.get("name") or "",
                                "error": str(obj.get("error")),
                                "requestId": obj.get("requestId"),
                            }
                            continue
                        yield {
                            "name": obj.get("name") or "",
                            "content": obj.get("content") or "",
                            "references": obj.get("references") or [],
                        }
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"T3Q 본문 생성 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"T3Q 서버에 연결할 수 없습니다: {e}") from e
        if not done:
            # 완료 신호 없이 종료 = T3Q 측 중단 (요구사항 FUN-CADM-302004 "중단" 상태)
            raise T3qError("T3Q 본문 생성이 완료 신호([DONE]) 없이 중단되었습니다")


class T3qChatBackend(LLMBackend):
    """API-LLM-001 (OpenAI 호환) 기반 LLMBackend — hwpx 편집 모드용.

    로그인이 없으므로 orchestrator가 넘기는 token 인자는 무시한다.
    finish_reason=="length"(잘림)는 last_finish_reason으로 노출 —
    상위의 잘림 내성 사다리(JSONL·부분 복구)가 내용 기준으로도 대응한다.
    """

    CHAT_PATH = "/llms/v1/chat/completions"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or config.T3Q_BASE_URL).rstrip("/")
        self.timeout = timeout or config.T3Q_TIMEOUT
        self.model = model or config.T3Q_LLM_MODEL
        self._transport = transport
        self.last_finish_reason: str | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self._transport, verify=_tls_verify()
        )

    def _chat_body(
        self, query: str, history: list[dict] | None, stream: bool, opts: dict[str, Any]
    ) -> dict[str, Any]:
        messages = list(history or []) + [{"role": "user", "content": query}]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if config.LLM_MAX_TOKENS > 0:
            body["max_tokens"] = config.LLM_MAX_TOKENS
        # token 등 백엔드 계약 외 인자는 요청에 싣지 않는다
        opts.pop("token", None)
        body.update(opts)
        return body

    async def chat(self, query: str, history: list[dict] | None = None, **opts) -> str:
        """stream=false → choices[0].message.content 반환."""
        body = self._chat_body(query, history, stream=False, opts=opts)
        try:
            async with self._client() as client:
                r = await client.post(f"{self.base_url}{self.CHAT_PATH}", json=body)
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"T3Q LLM 응답 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"T3Q 서버에 연결할 수 없습니다: {e}") from e
        _raise_for_status(r, "API-LLM-001")
        try:
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as e:
            raise T3qError(f"LLM 응답 형식이 예상과 다릅니다: {r.text[:200]!r}") from e
        self.last_finish_reason = choice.get("finish_reason")
        if not isinstance(content, str):
            raise T3qError(f"LLM 응답 content가 문자열이 아닙니다: {type(content).__name__}")
        return content

    async def chat_stream(
        self, query: str, history: list[dict] | None = None, **opts
    ) -> AsyncIterator[str]:
        """stream=true → OpenAI SSE delta.content를 순차 yield."""
        body = self._chat_body(query, history, stream=True, opts=opts)
        self.last_finish_reason = None
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", f"{self.base_url}{self.CHAT_PATH}", json=body
                ) as r:
                    if r.status_code != 200:
                        await r.aread()
                        _raise_for_status(r, "API-LLM-001")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if not payload:
                            continue
                        if payload == "[DONE]":
                            return
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        try:
                            choice = obj["choices"][0]
                        except (KeyError, IndexError, TypeError):
                            continue
                        if choice.get("finish_reason"):
                            self.last_finish_reason = choice["finish_reason"]
                        delta = (choice.get("delta") or {}).get("content")
                        if isinstance(delta, str) and delta:
                            yield delta
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"T3Q LLM 응답 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"T3Q 서버에 연결할 수 없습니다: {e}") from e
