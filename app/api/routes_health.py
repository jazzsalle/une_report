"""GET /api/health — UNI RAG 연결 상태 포함 헬스체크."""
import httpx
from fastapi import APIRouter

from app import config

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    rag = "down"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{config.UNI_RAG_BASE_URL}/health")
            if r.status_code == 200:
                rag = "up"
    except httpx.HTTPError:
        pass
    return {"status": "ok", "uni_rag": rag}
