"""문서 업로드·미리보기·내보내기. 코어 로직은 Phase 4~6에서 구현."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_current_user, get_db

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def list_documents(
    db: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(get_current_user)
):
    rows = db.execute(
        "SELECT id AS document_id, title, updated_at FROM documents WHERE user_id = ? ORDER BY updated_at DESC",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def upload_document(
    file: UploadFile,
    db: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(get_current_user),
):
    raise HTTPException(status_code=501, detail="Phase 6에서 구현")


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: str,
    page: int | None = None,
    db: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(get_current_user),
):
    raise HTTPException(status_code=501, detail="Phase 6에서 구현")
