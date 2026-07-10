"""POST /api/auth/login — UNI RAG에 위임 인증. Phase 5에서 실연동 구현."""
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.db.database import now_iso

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    account: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    from app.llm.uni_rag_client import UniRagAuthError, UniRagClient

    try:
        client = UniRagClient()
        rag_jwt = await client.login(body.account, body.password)
    except UniRagAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    db.execute(
        "INSERT INTO users(account, user_name, created_at, last_login_at) VALUES(?,?,?,?) "
        "ON CONFLICT(account) DO UPDATE SET last_login_at = excluded.last_login_at",
        (body.account, body.account, now_iso(), now_iso()),
    )
    user_id = db.execute("SELECT id FROM users WHERE account = ?", (body.account,)).fetchone()["id"]
    db.execute(
        "INSERT INTO auth_tokens(user_id, rag_jwt, issued_at) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET rag_jwt = excluded.rag_jwt, issued_at = excluded.issued_at",
        (user_id, rag_jwt, now_iso()),
    )
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO app_tokens(token, user_id, created_at) VALUES(?,?,?)", (token, user_id, now_iso()))
    db.commit()
    return {"token": token, "user_name": body.account}
