"""SQLite 연결·초기화. 접근은 이 모듈을 통해서만 한다 (Postgres 이관 대비 캡슐화)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import config

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
