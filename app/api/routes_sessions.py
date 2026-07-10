"""GET /api/sessions/{id}/messages — 세션 대화 이력 조회 (M5-1).

소유자가 아니거나 없는 세션은 존재 여부를 노출하지 않고 404로 답한다
(documents 라우터와 동일한 규약).
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, get_db

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    db: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(get_current_user),
):
    sess = db.execute(
        "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if sess is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    rows = db.execute(
        "SELECT role, content, intent, created_at FROM messages"
        " WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]
