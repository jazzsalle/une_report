"""공용 의존성: DB 연결, 익명 단일 사용자.

로그인 기능은 T3Q 전환(docs/t3q_upgrade_design.md §3)에서 삭제되었다.
모든 요청은 자동 생성되는 로컬 사용자("local") 소유로 처리한다 —
단일 사용자 로컬 도구 전제. users 등 기존 테이블은 스키마 유지(무마이그레이션).
"""
import sqlite3

from fastapi import Depends, Request

from app.db.database import now_iso

LOCAL_USER_ACCOUNT = "local"


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_current_user(db: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """익명 단일 로컬 사용자를 반환한다 (없으면 생성)."""
    row = db.execute(
        "SELECT * FROM users WHERE account = ?", (LOCAL_USER_ACCOUNT,)
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO users(account, user_name, created_at) VALUES(?,?,?)",
            (LOCAL_USER_ACCOUNT, "로컬 사용자", now_iso()),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM users WHERE account = ?", (LOCAL_USER_ACCOUNT,)
        ).fetchone()
    return row
