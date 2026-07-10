"""UNI RAG System API 클라이언트. Phase 5에서 전체 구현."""
import httpx

from app import config


class UniRagError(Exception):
    """UNI RAG 호출 실패."""


class UniRagAuthError(UniRagError):
    """인증 실패 (재로그인 필요)."""


class UniRagClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or config.UNI_RAG_BASE_URL).rstrip("/")
        self.timeout = timeout or config.UNI_RAG_TIMEOUT

    async def login(self, account: str, password: str) -> str:
        """POST /auth/login → JWT 반환."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
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
