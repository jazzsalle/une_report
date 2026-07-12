"""UNI RAG System API 클라이언트 (M3-2, M3-3, M3-5).

`LLMBackend` 구현체. 응답 스키마는 `docs/uni_rag_chat_schema.md` 실측 기준:

- stream=false: `{"answer": str, "sources": [...]}` — `answer`만 사용
- stream=true (SSE): `data:` 라인만 존재. 토큰 델타는 JSON 문자열 리터럴
  (`data: "토큰"` → json.loads 필요), `data: {"__sources__": [...]}` 1회,
  종료는 `data: [DONE]` (비JSON 리터럴)

에러 매핑 (M3-5):

- `httpx.TimeoutException`      → `LlmTimeoutError`
- HTTP 401/403                  → `UniRagAuthError` (자동 재로그인 없이 상위 전파)
- HTTP 5xx                      → `LlmUnavailableError` (상태 코드 포함)
- `httpx.ConnectError` 등 연결 불가 → `LlmUnavailableError`
"""
import json
from typing import Any, AsyncIterator

import httpx

from app import config
from app.llm.base import (
    LLMBackend,
    LlmAuthError,
    LlmError,
    LlmTimeoutError,
    LlmUnavailableError,
)


class UniRagError(LlmError):
    """UNI RAG 호출 실패 (하위 호환용 이름 — `LlmError` 계열)."""


class UniRagAuthError(UniRagError, LlmAuthError):
    """인증 실패 (재로그인 필요) — `LlmAuthError` 계열."""


class UniRagClient(LLMBackend):
    """UNI RAG `/auth/login`·`/chat/` 래퍼.

    history는 [{"role","content"}] stateless로 클라이언트가 관리하며
    session_id는 사용하지 않는다 (스키마 실측 §4 — 서버에 세션 메타 필드 없음).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        model_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or config.UNI_RAG_BASE_URL).rstrip("/")
        self.timeout = timeout or config.UNI_RAG_TIMEOUT
        self.model_key = model_key or config.UNI_RAG_MODEL_KEY
        # 테스트 주입 지점: httpx.MockTransport 등을 넣어 네트워크 없이 검증한다.
        self._transport = transport
        # chat_stream()에서 수신한 마지막 __sources__ (RAG 근거, 답변에 미포함)
        self.last_sources: list[dict] | None = None

    # ── 내부 헬퍼 ──────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _raise_for_chat_status(status_code: int) -> None:
        """/chat/ 응답 상태 코드를 예외 매트릭스에 따라 매핑한다."""
        if status_code in (401, 403):
            raise UniRagAuthError("UNI RAG 토큰 만료 또는 인증 실패 — 재로그인 필요")
        if status_code >= 500:
            raise LlmUnavailableError(f"UNI RAG 서버 오류 (HTTP {status_code})")
        if status_code != 200:
            raise LlmError(f"UNI RAG /chat/ 호출 실패 (HTTP {status_code})")

    def _chat_body(
        self,
        query: str,
        history: list[dict] | None,
        model_key: str | None,
        top_k: int,
        stream: bool,
        opts: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": query,
            "model_key": model_key or self.model_key,
            "history": history or [],
            "stream": stream,
            "top_k": top_k,
            "thinking": False,
        }
        # 생성 길이 상한: 서버 기본값에 응답이 잘리는 문제 대응 (config 주석 참조)
        if config.LLM_MAX_TOKENS > 0:
            body["max_tokens"] = config.LLM_MAX_TOKENS
        body.update(opts)  # 추가 옵션은 요청 바디에 그대로 병합 (max_tokens 재정의 가능)
        return body

    # ── 인증 (기존 시그니처·동작 유지 — routes_auth.py 사용 중) ──

    async def login(self, account: str, password: str) -> str:
        """POST /auth/login → JWT 반환."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self.base_url}/auth/login",
                    json={"account": account, "password": password},
                )
        except httpx.HTTPError as e:
            raise UniRagError(f"UNI RAG 서버에 연결할 수 없습니다: {e}") from e
        if r.status_code in (400, 401, 403, 422):
            raise UniRagAuthError("계정 또는 비밀번호가 올바르지 않습니다")
        if r.status_code != 200:
            raise UniRagError(f"로그인 실패 (HTTP {r.status_code})")
        data = r.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise UniRagError(f"로그인 응답에서 토큰을 찾지 못했습니다: {list(data.keys())}")
        return token

    # ── 대화 (M3-2: 비스트리밍 — 1차 파이프라인의 기본 경로) ──

    async def chat(
        self,
        query: str,
        history: list[dict] | None = None,
        *,
        token: str,
        model_key: str | None = None,
        top_k: int = 5,
        **opts,
    ) -> str:
        """POST /chat/ (stream=false) → answer 텍스트 반환."""
        body = self._chat_body(query, history, model_key, top_k, stream=False, opts=opts)
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self.base_url}/chat/",
                    json=body,
                    headers=self._auth_headers(token),
                )
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"UNI RAG 응답 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"UNI RAG 서버에 연결할 수 없습니다: {e}") from e
        self._raise_for_chat_status(r.status_code)
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise LlmError(f"/chat/ 응답이 JSON이 아닙니다: {r.text[:200]!r}") from e
        answer = data.get("answer") if isinstance(data, dict) else None
        if not isinstance(answer, str):
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise LlmError(f"/chat/ 응답에서 answer를 찾지 못했습니다: {keys}")
        return answer

    # ── 대화 (M3-3: SSE 스트리밍) ──────────────────────────────
    # 1차 파이프라인은 chat() 비스트리밍을 사용한다.
    # 스트리밍은 UI 개선용(채팅 타이핑 효과)으로만 쓴다.

    async def chat_stream(
        self,
        query: str,
        history: list[dict] | None = None,
        *,
        token: str,
        model_key: str | None = None,
        top_k: int = 5,
        **opts,
    ) -> AsyncIterator[str]:
        """POST /chat/ (stream=true) → 토큰 델타를 순차 yield.

        SSE 파서 규칙 (docs/uni_rag_chat_schema.md §2 실측):
        `data: [DONE]` → 종료 / JSON 문자열 → 델타 yield /
        `{"__sources__": ...}` → `self.last_sources`에 보관(답변 미포함).
        델타는 공백·구두점도 개별 이벤트이므로 구분자 없이 그대로 잇는다.
        """
        body = self._chat_body(query, history, model_key, top_k, stream=True, opts=opts)
        self.last_sources = None
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/",
                    json=body,
                    headers=self._auth_headers(token),
                ) as r:
                    if r.status_code != 200:
                        await r.aread()
                        self._raise_for_chat_status(r.status_code)
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
                            continue  # [DONE] 외의 비JSON 라인은 무시 (방어적)
                        if isinstance(obj, str):
                            yield obj  # 답변 토큰 델타 (json.loads가 이스케이프 처리)
                        elif isinstance(obj, dict) and "__sources__" in obj:
                            self.last_sources = obj["__sources__"]
        except httpx.TimeoutException as e:
            raise LlmTimeoutError(f"UNI RAG 응답 시간 초과 ({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LlmUnavailableError(f"UNI RAG 서버에 연결할 수 없습니다: {e}") from e
