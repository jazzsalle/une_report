"""GET /api/health — T3Q 플랫폼 연결 상태 포함 헬스체크."""
import httpx
from fastapi import APIRouter

from app import config
from app.llm.t3q_client import _tls_verify

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    t3q = "down"
    try:
        async with httpx.AsyncClient(timeout=5, verify=_tls_verify()) as client:
            # 전용 헬스 엔드포인트가 없어 베이스 URL 응답 여부로 연결만 확인
            r = await client.get(config.T3Q_BASE_URL)
            if r.status_code < 500:
                t3q = "up"
    except httpx.HTTPError:
        pass
    return {"status": "ok", "t3q": t3q}
