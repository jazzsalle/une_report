"""시나리오 A·B end-to-end 테스트 (DESIGN.md §3) — FakeBackend, 네트워크 0회.

업로드 → 채팅 지시(SSE) → document_updated → export까지 전 구간을
실제 라우터 스택(documents + chat + sessions)으로 검증한다.
LLM만 FakeBackend로 바꿔치기하고, hwpx 파싱·편집·재패키징(M1),
문서 저장소(M5), 오케스트레이터(M4)는 전부 실물이 돈다.

- 시나리오 A: 양식 업로드 → "초안 작성해줘"(fill) → 추가 지시(edit)
  → export hwpx 유효 + placeholder 전부 해소
- 시나리오 B: 기존 문서 업로드 → 특정 문단 수정(edit) → export 재파싱
  시 신규 텍스트 존재 + 미편집 노드 원문 보존
"""
import json
import secrets
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_chat, routes_documents, routes_sessions
from app.core.hwpx import (
    collect_placeholders,
    extract_hwpx,
    find_section_files,
    parse_section,
    validate_hwpx,
)
from app.db import database
from app.services.document_store import DocumentStore
from tests.test_chat_api import parse_sse
from tests.test_orchestrator import FakeBackend


# ---------------------------------------------------------------------------
# 픽스처·헬퍼
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path):
    """(TestClient, db, files_dir, app) — documents+chat+sessions 전체 스택."""
    db = database.connect(tmp_path / "test_e2e.db")
    database.init_db(db)
    files_dir = tmp_path / "files"

    app = FastAPI()
    app.include_router(routes_documents.router, prefix="/api")
    app.include_router(routes_chat.router, prefix="/api")
    app.include_router(routes_sessions.router, prefix="/api")
    app.state.db = db
    app.state.files_dir = files_dir

    client = TestClient(app)
    yield client, db, files_dir, app
    db.close()


def _make_user(db: sqlite3.Connection, account: str) -> tuple[int, str]:
    now = database.now_iso()
    db.execute(
        "INSERT INTO users(account, user_name, created_at) VALUES(?,?,?)", (account, account, now)
    )
    user_id = db.execute("SELECT id FROM users WHERE account = ?", (account,)).fetchone()["id"]
    token = secrets.token_urlsafe(16)
    db.execute("INSERT INTO app_tokens(token, user_id, created_at) VALUES(?,?,?)", (token, user_id, now))
    db.execute(
        "INSERT INTO auth_tokens(user_id, rag_jwt, issued_at) VALUES(?,?,?)",
        (user_id, f"rag-jwt-{account}", now),
    )
    db.commit()
    return user_id, token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: TestClient, token: str, hwpx_path: Path) -> dict:
    resp = client.post(
        "/api/documents",
        headers=_auth(token),
        files={"file": (hwpx_path.name, hwpx_path.read_bytes(), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _chat(client: TestClient, token: str, payload: dict) -> dict[str, dict]:
    """SSE 응답을 {event: data} dict로 반환한다 (이벤트 순서도 검증)."""
    resp = client.post("/api/chat", headers=_auth(token), json=payload)
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "done", names
    assert "error" not in names, events
    return dict(events)


def _export_hwpx(client: TestClient, token: str, doc_id: str, save_to: Path) -> Path:
    resp = client.post(
        f"/api/documents/{doc_id}/export", headers=_auth(token), json={"format": "hwpx"}
    )
    assert resp.status_code == 200, resp.text
    save_to.write_bytes(resp.content)
    return save_to


def _node_texts(hwpx_path: Path, work_dir: Path) -> dict[int, str]:
    """{전역 id: text} — 섹션 offset 누적 (M1 순번 규칙)."""
    extract_dir = work_dir / f"nodes_{hwpx_path.stem}"
    extract_hwpx(hwpx_path, extract_dir)
    out: dict[int, str] = {}
    offset = 0
    for sf in find_section_files(extract_dir):
        nodes, _tree, _pm, _ns = parse_section(sf)
        for n in nodes:
            out[offset + n.id] = n.text
        offset += len(nodes)
    return out


def _remaining_placeholders(hwpx_path: Path, work_dir: Path) -> list:
    """export된 hwpx를 재파싱해 잔존 placeholder를 수집한다."""
    extract_dir = work_dir / f"ph_{hwpx_path.stem}"
    extract_hwpx(hwpx_path, extract_dir)
    hits = []
    offset = 0
    for sf in find_section_files(extract_dir):
        nodes, _tree, _pm, _ns = parse_section(sf)
        hits.extend(collect_placeholders(nodes, id_offset=offset))
        offset += len(nodes)
    return hits


# demo_form_hwpx의 placeholder 표식 → 채움 텍스트 (어떤 패턴에도 재매칭되지 않는 값)
_FILL_TEXTS = {
    "[기관명]": "수립 기관: 서울특별시 재난안전대책본부",
    "○○시설": "대상 시설: 시민종합회관",
    "<담당자 이름>": "담당자: 김철수",
    "YYYY년 MM월 DD일": "작성일: 2026년 7월 11일",
    "TBD": "02-1234-5678",
    "[주소 입력]": "서울특별시 중구 세종대로 110",
}


# ---------------------------------------------------------------------------
# 시나리오 A — 표준 양식 채움 (fill → edit → export)
# ---------------------------------------------------------------------------

def test_scenario_a_fill_form_then_edit_then_export(env, demo_form_hwpx, tmp_path):
    client, db, files_dir, app = env
    user_id, token = _make_user(db, "scenario-a")

    # 1) 양식 업로드
    doc = _upload(client, token, demo_form_hwpx)
    doc_id = doc["document_id"]

    # placeholder 노드 id → 채움 텍스트 매핑 (FakeBackend 응답 구성용)
    store = DocumentStore(db, files_dir=files_dir)
    nodes = {n["id"]: n for n in store.get_nodes(user_id, doc_id)}
    ph_ids = sorted({p["id"] for p in store.get_placeholders(user_id, doc_id)})
    assert ph_ids, "데모 양식에 placeholder가 있어야 한다"
    fill_edits = []
    for gid in ph_ids:
        text = nodes[gid]["text"]
        replacement = next(v for k, v in _FILL_TEXTS.items() if k in text)
        fill_edits.append({"id": gid, "new_text": replacement})

    backend = FakeBackend([
        '{"intent": "fill"}',
        json.dumps({"reply": "초안을 작성했습니다", "edits": fill_edits}, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    # 2) 채움 지시 → document_updated(version 1)
    ev = _chat(client, token, {
        "document_id": doc_id,
        "message": "이 양식으로 재난대응 계획서 초안 작성해줘. 시설 개요는 시민종합회관이야.",
    })
    assert ev["status"]["intent"] == "fill"
    upd = ev["document_updated"]
    assert upd["document_id"] == doc_id
    assert upd["version"] == 1
    assert sorted(upd["changed_ids"]) == ph_ids  # placeholder 노드 전부 반영
    assert "수립 기관: 서울특별시 재난안전대책본부" in upd["html"]
    assert "[기관명]" not in upd["html"]
    session_id = ev["done"]["session_id"]

    # 3) 추가 지시 (같은 세션) → version 2
    manager_id = next(
        gid for gid, n in nodes.items() if "<담당자 이름>" in n["text"]
    )
    backend.responses.extend([
        # 병합 응답: 분류+편집이 한 호출에 담긴다
        json.dumps({
            "intent": "edit",
            "reply": "담당자를 홍길동으로 바꿨습니다",
            "edits": [{"id": manager_id, "new_text": "담당자: 홍길동"}],
        }, ensure_ascii=False),
    ])
    ev2 = _chat(client, token, {
        "session_id": session_id,
        "document_id": doc_id,
        "message": "담당자를 홍길동으로 바꿔줘",
    })
    assert ev2["status"]["intent"] == "edit"
    assert ev2["document_updated"]["version"] == 2
    assert ev2["document_updated"]["changed_ids"] == [manager_id]
    assert "담당자: 홍길동" in ev2["document_updated"]["html"]

    # 4) export → 유효한 hwpx + 편집 텍스트 반영 + placeholder 전부 해소
    exported = _export_hwpx(client, token, doc_id, tmp_path / "scenario_a.hwpx")
    validation = validate_hwpx(exported)
    assert validation.ok, validation.errors

    texts = _node_texts(exported, tmp_path)
    assert "담당자: 홍길동" in texts.values()
    assert "수립 기관: 서울특별시 재난안전대책본부" in texts.values()
    assert "02-1234-5678" in texts.values()

    remaining = _remaining_placeholders(exported, tmp_path)
    assert remaining == [], f"placeholder가 남아 있음: {remaining}"
    # 저장소 계층에서도 동일하게 해소 확인
    assert store.get_placeholders(user_id, doc_id) == []

    # 5) 버전 이력: v0(원본) → v1(채움) → v2(담당자 수정)
    versions = client.get(f"/api/documents/{doc_id}/versions", headers=_auth(token)).json()
    assert [v["version"] for v in versions] == [0, 1, 2]


# ---------------------------------------------------------------------------
# 시나리오 B — 기존 문서 대화 수정 (edit → export, 원문 보존)
# ---------------------------------------------------------------------------

def test_scenario_b_edit_existing_document_preserves_rest(env, demo_form_hwpx, tmp_path):
    client, db, files_dir, app = env
    user_id, token = _make_user(db, "scenario-b")

    doc = _upload(client, token, demo_form_hwpx)
    doc_id = doc["document_id"]

    store = DocumentStore(db, files_dir=files_dir)
    original = {n["id"]: n["text"] for n in store.get_nodes(user_id, doc_id)}
    target_id = next(
        gid for gid, text in original.items() if "본 문서는 단위 테스트용" in text
    )

    backend = FakeBackend([
        # 병합 응답: 분류+편집이 한 호출에 담긴다
        json.dumps({
            "intent": "edit",
            "reply": "해당 문단을 수정했습니다",
            "edits": [{"id": target_id, "new_text": "hwpx 문서 생성 고도화"}],
        }, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {
        "document_id": doc_id,
        "message": "개요 문단을 'hwpx 문서 생성 고도화'로 수정해줘",
    })
    assert ev["status"]["intent"] == "edit"
    assert ev["document_updated"]["version"] == 1
    assert ev["document_updated"]["changed_ids"] == [target_id]

    exported = _export_hwpx(client, token, doc_id, tmp_path / "scenario_b.hwpx")
    validation = validate_hwpx(exported)
    assert validation.ok, validation.errors

    texts = _node_texts(exported, tmp_path)
    # 대상 노드만 바뀌고
    assert texts[target_id] == "hwpx 문서 생성 고도화"
    # 나머지 노드는 원문 그대로 보존된다
    untouched = {gid: t for gid, t in original.items() if gid != target_id}
    assert {gid: texts[gid] for gid in untouched} == untouched

    # 대화 이력도 세션에 남는다
    session_id = ev["done"]["session_id"]
    msgs = client.get(f"/api/sessions/{session_id}/messages", headers=_auth(token)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["intent"] == "edit"


# ---------------------------------------------------------------------------
# 앱 조립 스모크 — SPA 정적 서빙 + 헬스체크
# ---------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@pytest.mark.skipif(not _DIST.is_dir(), reason="web/dist 빌드 산출물 없음 (cd web && npm run build)")
def test_app_serves_spa_and_health(tmp_path, monkeypatch):
    """create_app() 실물 조립: /가 index.html을 서빙하고 /api/health가 200."""
    from app import config
    from app.main import create_app

    # 전역 data/ 디렉터리를 오염시키지 않도록 임시 경로로 재지정
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # 헬스체크의 UNI RAG 프로브가 외부로 나가지 않게 즉시 실패 주소로 재지정
    monkeypatch.setattr(config, "UNI_RAG_BASE_URL", "http://127.0.0.1:9")

    app = create_app()
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        index = client.get("/")
        assert index.status_code == 200
        assert index.headers["content-type"].startswith("text/html")
        assert "<div id=" in index.text  # Vue 마운트 포인트
