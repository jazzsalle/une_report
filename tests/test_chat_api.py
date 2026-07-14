"""chat SSE API + 세션 영속 테스트 (FakeBackend 주입, 네트워크 호출 0회).

임시 SQLite로 최소 앱을 조립하고 dependency_overrides로 LLM 백엔드를
바꿔치기해, SSE 이벤트 순서(status → token → [document_updated] → done)와
오류 이벤트 매핑·세션/메시지 영속을 검증한다.
"""
import json
import secrets
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_chat, routes_sessions
from app.db import database
from app.llm.base import (
    LlmAuthError,
    LlmJsonParseError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from tests.test_orchestrator import FakeBackend


# ---------------------------------------------------------------------------
# 픽스처·헬퍼
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path):
    """(TestClient, db, app) — chat/sessions 라우터만 얹은 최소 앱."""
    db = database.connect(tmp_path / "test_chat.db")
    database.init_db(db)

    app = FastAPI()
    app.include_router(routes_chat.router, prefix="/api")
    app.include_router(routes_sessions.router, prefix="/api")
    app.state.db = db
    app.state.files_dir = tmp_path / "files"

    client = TestClient(app)
    yield client, db, app
    db.close()


def _make_user(db: sqlite3.Connection, account: str, rag_jwt: str | None = None) -> tuple[int, str]:
    """익명 단일 로컬 사용자를 보장하고 (user_id, 더미 토큰) 반환.

    로그인 삭제(T3Q 전환) — account·rag_jwt 인자는 기존 호출부 호환용이며 무시된다.
    """
    from app.api.deps import LOCAL_USER_ACCOUNT

    now = database.now_iso()
    db.execute(
        "INSERT OR IGNORE INTO users(account, user_name, created_at) VALUES(?,?,?)",
        (LOCAL_USER_ACCOUNT, "로컬 사용자", now),
    )
    user_id = db.execute(
        "SELECT id FROM users WHERE account = ?", (LOCAL_USER_ACCOUNT,)
    ).fetchone()["id"]
    db.commit()
    return user_id, "no-auth"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _use_backend(app: FastAPI, backend: FakeBackend) -> None:
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """SSE 본문을 [(event, data dict)] 목록으로 파싱한다 (빈 줄이 이벤트 경계)."""
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        events.append((name, json.loads("\n".join(data_lines))))
    return events


def _chat(client: TestClient, token: str, payload: dict) -> list[tuple[str, dict]]:
    resp = client.post("/api/chat", headers=_auth(token), json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


# ---------------------------------------------------------------------------
# 정상 흐름 — 문서 없는 query 대화
# ---------------------------------------------------------------------------

class TestQueryChat:
    def test_query_without_document_event_order(self, env):
        client, db, app = env
        _, token = _make_user(db, "asker")
        _use_backend(app, FakeBackend(["hwpx는 한글 문서 표준 포맷입니다."]))

        events = _chat(client, token, {"message": "hwpx가 뭐야?"})
        assert [name for name, _ in events] == ["status", "token", "done"]

        status, token_ev, done = events[0][1], events[1][1], events[2][1]
        assert status["intent"] == "query"
        assert isinstance(status["detail"], str) and status["detail"]
        assert token_ev["text"] == "hwpx는 한글 문서 표준 포맷입니다."
        assert done["session_id"]  # 서버가 uuid hex 발급

    def test_backend_called_without_auth_token(self, env):
        """로그인 삭제 — 백엔드에는 빈 토큰이 전달된다 (계약 유지용)."""
        client, db, app = env
        _, token = _make_user(db, "jwt-user")
        backend = FakeBackend(["답"])
        _use_backend(app, backend)

        _chat(client, token, {"message": "질문"})
        assert backend.calls[0][2]["token"] == ""


# ---------------------------------------------------------------------------
# 오류 이벤트 매핑
# ---------------------------------------------------------------------------

class TestErrorEvents:
    @pytest.mark.parametrize("exc,code", [
        (LlmAuthError("토큰 만료"), "auth"),
        (LlmTimeoutError("120s 초과"), "llm_timeout"),
        (LlmUnavailableError("HTTP 503"), "llm_unavailable"),
        (LlmJsonParseError("복구 불가 JSON"), "llm_unavailable"),
    ])
    def test_llm_exception_mapped_to_error_code(self, env, exc, code):
        client, db, app = env
        _, token = _make_user(db, f"err-{code}-{type(exc).__name__}")
        _use_backend(app, FakeBackend([exc]))

        events = _chat(client, token, {"message": "질문"})
        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == code
        assert events[-1][1]["message"]

    def test_json_parse_error_message_includes_reason(self, env):
        client, db, app = env
        _, token = _make_user(db, "json-err")
        _use_backend(app, FakeBackend([LlmJsonParseError("복구 불가 JSON")]))

        events = _chat(client, token, {"message": "질문"})
        assert "복구 불가 JSON" in events[-1][1]["message"]

    def test_empty_message_yields_bad_request(self, env):
        client, db, app = env
        _, token = _make_user(db, "empty-msg")
        _use_backend(app, FakeBackend([]))

        events = _chat(client, token, {"message": "   "})
        assert events == [("error", events[0][1])]
        assert events[0][1]["code"] == "bad_request"

    def test_unknown_document_yields_bad_request(self, env):
        client, db, app = env
        _, token = _make_user(db, "no-doc")
        _use_backend(app, FakeBackend(["안 옴"]))

        events = _chat(client, token, {"message": "수정해줘", "document_id": "no-such-doc"})
        assert events[0][0] == "error"
        assert events[0][1]["code"] == "bad_request"

    def test_anonymous_access_allowed(self, env):
        """로그인 삭제 — 인증 헤더 없이도 채팅이 동작한다 (익명 단일 사용자)."""
        client, _, app = env
        _use_backend(app, FakeBackend(["익명 답변"]))
        resp = client.post("/api/chat", json={"message": "안녕"})
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        assert [n for n, _ in events] == ["status", "token", "done"]


# ---------------------------------------------------------------------------
# 세션 영속·재사용
# ---------------------------------------------------------------------------

class TestSessionPersistence:
    def test_messages_persisted_and_listed(self, env):
        client, db, app = env
        _, token = _make_user(db, "persist")
        _use_backend(app, FakeBackend(["첫 번째 답변"]))

        events = _chat(client, token, {"message": "첫 번째 질문"})
        session_id = events[-1][1]["session_id"]

        resp = client.get(f"/api/sessions/{session_id}/messages", headers=_auth(token))
        assert resp.status_code == 200
        msgs = resp.json()
        assert [(m["role"], m["content"]) for m in msgs] == [
            ("user", "첫 번째 질문"),
            ("assistant", "첫 번째 답변"),
        ]
        assert msgs[0]["intent"] is None
        assert msgs[1]["intent"] == "query"
        assert all(m["created_at"] for m in msgs)

    def test_session_reuse_accumulates_and_feeds_history(self, env):
        client, db, app = env
        _, token = _make_user(db, "reuse")
        backend = FakeBackend(["답변 하나", "답변 둘"])
        _use_backend(app, backend)

        events1 = _chat(client, token, {"message": "질문 하나"})
        session_id = events1[-1][1]["session_id"]
        events2 = _chat(client, token, {"message": "질문 둘", "session_id": session_id})
        assert events2[-1][1]["session_id"] == session_id  # 같은 세션 유지

        # 두 번째 호출에는 첫 턴의 대화 이력이 history로 실린다
        assert backend.calls[1][1] == [
            {"role": "user", "content": "질문 하나"},
            {"role": "assistant", "content": "답변 하나"},
        ]

        msgs = client.get(f"/api/sessions/{session_id}/messages", headers=_auth(token)).json()
        assert [m["content"] for m in msgs] == ["질문 하나", "답변 하나", "질문 둘", "답변 둘"]

        # 세션 행은 1개만 존재
        count = db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        assert count == 1

    def test_unknown_session_messages_404(self, env):
        """존재하지 않는 세션 id 조회는 404 (익명 단일 사용자 체계)."""
        client, db, app = env
        _, token = _make_user(db, "sess-owner")
        _use_backend(app, FakeBackend(["답"]))

        _chat(client, token, {"message": "대화"})
        resp = client.get("/api/sessions/no-such-session/messages", headers=_auth(token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 진행 status 이벤트 (fill 청크 진행 중계)
# ---------------------------------------------------------------------------

class TestProgressStatus:
    def _upload_doc(self, client, token, tmp_path):
        """문서 라우터 없이도 되는 테스트용 — 문서 없는 fill은 불가하므로
        문서 컨텍스트가 필요한 시나리오는 e2e 픽스처를 쓴다. 여기서는
        문서 없이 query만 확인하는 대신, doc 관련 진행 테스트는
        test_e2e_scenarios 쪽 헬퍼를 재사용한다."""

    def test_query_turn_has_no_progress_status(self, env):
        """진행 통지가 없는 턴(query)은 기존 이벤트 순서 그대로다."""
        client, db, app = env
        _, token = _make_user(db, "no-progress")
        _use_backend(app, FakeBackend(["그냥 답변"]))
        events = _chat(client, token, {"message": "안녕?"})
        assert [name for name, _ in events] == ["status", "token", "done"]
