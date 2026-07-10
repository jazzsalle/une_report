"""공용 의존성: DB 연결, Bearer 토큰 인증."""
import sqlite3

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_current_user(
    db: sqlite3.Connection = Depends(get_db),
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> sqlite3.Row:
    if cred is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    row = db.execute(
        "SELECT u.* FROM app_tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?",
        (cred.credentials,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")
    return row
