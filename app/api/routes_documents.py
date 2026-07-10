"""문서 업로드·미리보기·버전·내보내기 (M5-2 REST 계층).

저장·변환 로직은 DocumentStore에 위임하고, 여기서는 오류 규약만 매핑한다:
ValueError → 400 (잘못된 hwpx·미지원 형식), KeyError → 404 (없음·소유자 아님).
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_current_user, get_db
from app.services.document_store import DocumentStore

router = APIRouter(prefix="/documents", tags=["documents"])

_MEDIA_TYPES = {
    ".hwpx": "application/octet-stream",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def get_store(request: Request, db: sqlite3.Connection = Depends(get_db)) -> DocumentStore:
    """DocumentStore 의존성. 테스트는 app.state.files_dir로 저장 위치를 바꿀 수 있다."""
    files_dir = getattr(request.app.state, "files_dir", None)
    return DocumentStore(db, files_dir=files_dir)


def _key_error_detail(e: KeyError) -> str:
    return str(e.args[0]) if e.args else "문서를 찾을 수 없습니다"


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
    store: DocumentStore = Depends(get_store),
    user: sqlite3.Row = Depends(get_current_user),
):
    data = await file.read()
    try:
        return store.create_document(user["id"], file.filename or "document.hwpx", data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: str,
    version: int | None = None,
    store: DocumentStore = Depends(get_store),
    user: sqlite3.Row = Depends(get_current_user),
):
    try:
        return store.get_preview(user["id"], document_id, version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=_key_error_detail(e))


@router.get("/{document_id}/versions")
async def list_versions(
    document_id: str,
    store: DocumentStore = Depends(get_store),
    user: sqlite3.Row = Depends(get_current_user),
):
    try:
        return store.list_versions(user["id"], document_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=_key_error_detail(e))


class ExportRequest(BaseModel):
    format: str = "hwpx"
    version: int | None = None


@router.post("/{document_id}/export")
async def export_document(
    document_id: str,
    body: ExportRequest,
    store: DocumentStore = Depends(get_store),
    user: sqlite3.Row = Depends(get_current_user),
):
    try:
        path, filename = store.export_file(user["id"], document_id, body.format, body.version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=_key_error_detail(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=filename)
