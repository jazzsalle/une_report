"""POST /api/chat — SSE 대화. Phase 6에서 오케스트레이터 연결."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user, get_db

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str | None = None
    document_id: str | None = None
    message: str
    selection: list[str] | None = None


@router.post("/chat")
async def chat(
    body: ChatRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(get_current_user),
):
    raise HTTPException(status_code=501, detail="Phase 6에서 구현")
