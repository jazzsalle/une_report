"""LLM 백엔드 추상 인터페이스와 공통 예외 계층 (M3-4, M3-6).

UNI RAG 외에 OpenAI 호환 엔드포인트 등으로 교체할 수 있도록,
모든 LLM 구현체는 `LLMBackend`를 구현하고 예외는 `LlmError` 계층으로 던진다.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator


# ── 공통 예외 계층 ──────────────────────────────────────────────

class LlmError(Exception):
    """LLM 호출 관련 공통 오류 (모든 LLM 예외의 최상위)."""


class LlmAuthError(LlmError):
    """인증 실패 (재로그인 또는 토큰 갱신 필요)."""


class LlmUnavailableError(LlmError):
    """LLM 서버 연결 불가 또는 일시적 장애 (5xx 등)."""


class LlmTimeoutError(LlmError):
    """LLM 응답 시간 초과."""


class LlmJsonParseError(LlmError):
    """LLM 응답을 JSON으로 복구/파싱하지 못함."""


# ── 백엔드 추상 인터페이스 ──────────────────────────────────────

class LLMBackend(ABC):
    """대화형 LLM 백엔드 인터페이스.

    구현체 예: UniRagBackend(UNI RAG /chat/), OpenAI 호환 엔드포인트 등.
    `history`는 [{"role": "user"|"assistant", "content": str}, ...] 형식을 따른다.
    """

    @abstractmethod
    async def chat(self, query: str, history: list[dict] | None = None, **opts) -> str:
        """단일 질의를 보내고 전체 응답 텍스트를 반환한다."""
        raise NotImplementedError

    @abstractmethod
    async def chat_stream(
        self, query: str, history: list[dict] | None = None, **opts
    ) -> AsyncIterator[str]:
        """단일 질의를 보내고 응답 텍스트 조각(chunk)을 순차적으로 낸다."""
        raise NotImplementedError
        yield ""  # pragma: no cover — async generator 시그니처 유지용
