"""documents API + DocumentStore 통합 테스트 (LLM·네트워크 무관).

임시 SQLite와 tmp_path 파일저장소로 최소 FastAPI 앱을 조립해
업로드 → 미리보기 → 편집(버전 증가) → 내보내기 흐름과
인증(401)·소유권(404) 규칙을 검증한다. 앱 전역 상태(config.DB_PATH 등)는
건드리지 않는다 — DB는 직접 주입, 파일 경로는 app.state.files_dir로 재지정.
"""
import secrets
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_documents
from app.core.hwpx import extract_hwpx, find_section_files, parse_section, validate_hwpx
from app.db import database
from app.services.document_store import DocumentStore


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path):
    """(TestClient, db 연결, files_dir) — documents 라우터만 얹은 최소 앱."""
    db = database.connect(tmp_path / "test_app.db")
    database.init_db(db)
    files_dir = tmp_path / "files"

    app = FastAPI()
    app.include_router(routes_documents.router, prefix="/api")
    app.state.db = db
    app.state.files_dir = files_dir  # get_store가 읽는 테스트용 재지정 지점

    client = TestClient(app)
    yield client, db, files_dir
    db.close()


def _make_user(db: sqlite3.Connection, account: str) -> tuple[int, str]:
    """익명 단일 로컬 사용자를 보장하고 (user_id, 더미 토큰)을 반환한다.

    로그인 삭제(T3Q 전환) — account 인자는 기존 호출부 호환용이며 무시된다.
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


def _upload(client: TestClient, token: str, hwpx_path: Path) -> dict:
    resp = client.post(
        "/api/documents",
        headers=_auth(token),
        files={"file": (hwpx_path.name, hwpx_path.read_bytes(), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


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


# ---------------------------------------------------------------------------
# 업로드·미리보기
# ---------------------------------------------------------------------------

class TestUploadAndPreview:
    def test_upload_returns_document_meta(self, env, demo_form_hwpx):
        client, db, _ = env
        _, token = _make_user(db, "uploader")
        body = _upload(client, token, demo_form_hwpx)
        assert body["document_id"]
        assert body["title"] == "demo_form"
        assert body["pages"] >= 1
        assert body["version"] == 0

    def test_upload_invalid_hwpx_returns_400(self, env):
        client, db, files_dir = env
        _, token = _make_user(db, "uploader")
        resp = client.post(
            "/api/documents",
            headers=_auth(token),
            files={"file": ("bad.hwpx", b"not a zip at all", "application/octet-stream")},
        )
        assert resp.status_code == 400
        # 실패한 업로드는 파일 잔재를 남기지 않는다
        assert not files_dir.exists() or not any(files_dir.iterdir())

    def test_preview_html_has_data_ids(self, env, demo_form_hwpx):
        client, db, _ = env
        _, token = _make_user(db, "viewer")
        doc = _upload(client, token, demo_form_hwpx)

        resp = client.get(f"/api/documents/{doc['document_id']}/preview", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 0
        assert body["page_count"] == doc["pages"]
        assert 'data-id="0"' in body["html"]
        assert "[기관명]" in body["html"]

    def test_list_documents(self, env, demo_form_hwpx):
        client, db, _ = env
        _, token = _make_user(db, "lister")
        doc = _upload(client, token, demo_form_hwpx)
        resp = client.get("/api/documents", headers=_auth(token))
        assert resp.status_code == 200
        assert [d["document_id"] for d in resp.json()] == [doc["document_id"]]


# ---------------------------------------------------------------------------
# 편집 → 버전 증가 → 미리보기 갱신
# ---------------------------------------------------------------------------

class TestApplyEdits:
    def test_apply_edits_bumps_version_and_updates_preview(self, env, demo_form_hwpx):
        client, db, files_dir = env
        user_id, token = _make_user(db, "editor")
        doc = _upload(client, token, demo_form_hwpx)
        doc_id = doc["document_id"]

        store = DocumentStore(db, files_dir=files_dir)
        nodes = store.get_nodes(user_id, doc_id)
        assert nodes and all(n["type"] in ("para", "cell") for n in nodes)
        target = next(n for n in nodes if "[기관명]" in n["text"])
        assert target["type"] == "para"

        result = store.apply_document_edits(
            user_id, doc_id,
            [{"id": target["id"], "new_text": "수립 기관: 서울특별시 재난안전대책본부"}],
            summary="기관명 채움",
        )
        assert result["version"] == 1
        assert result["applied_ids"] == [target["id"]]
        assert result["skipped_ids"] == []
        assert "서울특별시 재난안전대책본부" in result["html"]

        # REST 미리보기도 새 버전을 반영해야 한다
        resp = client.get(f"/api/documents/{doc_id}/preview", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 1
        assert "서울특별시 재난안전대책본부" in body["html"]
        assert "[기관명]" not in body["html"]

        # 이전 버전 미리보기는 원문 유지 (?version=0)
        resp0 = client.get(
            f"/api/documents/{doc_id}/preview", params={"version": 0}, headers=_auth(token)
        )
        assert resp0.status_code == 200
        assert "[기관명]" in resp0.json()["html"]

        # 버전 이력
        versions = client.get(f"/api/documents/{doc_id}/versions", headers=_auth(token)).json()
        assert [v["version"] for v in versions] == [0, 1]
        assert versions[1]["summary"] == "기관명 채움"

    def test_placeholders_shrink_after_fill(self, env, demo_form_hwpx):
        client, db, files_dir = env
        user_id, token = _make_user(db, "filler")
        doc = _upload(client, token, demo_form_hwpx)
        store = DocumentStore(db, files_dir=files_dir)

        before = store.get_placeholders(user_id, doc["document_id"])
        assert any(h["token"] == "[기관명]" for h in before)

        target = next(n for n in store.get_nodes(user_id, doc["document_id"]) if "[기관명]" in n["text"])
        store.apply_document_edits(
            user_id, doc["document_id"], [{"id": target["id"], "new_text": "수립 기관: 행정안전부"}]
        )
        after = store.get_placeholders(user_id, doc["document_id"])
        assert not any(h["token"] == "[기관명]" for h in after)
        assert len(after) < len(before)


# ---------------------------------------------------------------------------
# 내보내기
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_hwpx_roundtrip_keeps_edits(self, env, demo_form_hwpx, tmp_path):
        client, db, files_dir = env
        user_id, token = _make_user(db, "exporter")
        doc = _upload(client, token, demo_form_hwpx)
        doc_id = doc["document_id"]

        store = DocumentStore(db, files_dir=files_dir)
        target = next(n for n in store.get_nodes(user_id, doc_id) if "TBD" in n["text"])
        store.apply_document_edits(user_id, doc_id, [{"id": target["id"], "new_text": "02-1234-5678"}])

        resp = client.post(
            f"/api/documents/{doc_id}/export", headers=_auth(token), json={"format": "hwpx"}
        )
        assert resp.status_code == 200
        assert "content-disposition" in resp.headers
        assert ".hwpx" in resp.headers["content-disposition"]

        saved = tmp_path / "exported.hwpx"
        saved.write_bytes(resp.content)
        validation = validate_hwpx(saved)
        assert validation.ok, validation.errors
        texts = _node_texts(saved, tmp_path)
        assert "02-1234-5678" in texts.values()
        assert "TBD" not in texts.values()

    def test_export_docx(self, env, demo_form_hwpx, tmp_path):
        client, db, _ = env
        _, token = _make_user(db, "docx-user")
        doc = _upload(client, token, demo_form_hwpx)

        resp = client.post(
            f"/api/documents/{doc['document_id']}/export",
            headers=_auth(token), json={"format": "docx"},
        )
        assert resp.status_code == 200
        saved = tmp_path / "exported.docx"
        saved.write_bytes(resp.content)

        from docx import Document

        d = Document(str(saved))
        all_text = "\n".join(p.text for p in d.paragraphs)
        all_text += "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
        assert "재난 대응 계획서 (테스트)" in all_text
        assert "TBD" in all_text  # 표 셀 내용도 보존

    def test_export_unknown_format_returns_400(self, env, demo_form_hwpx):
        client, db, _ = env
        _, token = _make_user(db, "fmt-user")
        doc = _upload(client, token, demo_form_hwpx)
        resp = client.post(
            f"/api/documents/{doc['document_id']}/export",
            headers=_auth(token), json={"format": "pdf"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 접근 규칙 (로그인 삭제 — 익명 단일 사용자)
# ---------------------------------------------------------------------------

class TestAnonymousAccess:
    def test_anonymous_access_allowed(self, env, demo_form_hwpx):
        """인증 헤더 없이도 업로드·조회가 동작한다 (T3Q 전환 — 로그인 삭제)."""
        client, _, _ = env
        resp = client.post(
            "/api/documents",
            files={"file": ("demo.hwpx", demo_form_hwpx.read_bytes())},
        )
        assert resp.status_code == 200
        assert client.get("/api/documents").status_code == 200

    def test_stale_bearer_token_is_ignored(self, env):
        """구 클라이언트가 보내는 Bearer 헤더는 무시되고 정상 동작한다."""
        client, _, _ = env
        assert client.get("/api/documents", headers=_auth("no-such-token")).status_code == 200

    def test_store_unknown_owner_still_keyerror(self, env, demo_form_hwpx):
        """DocumentStore 계층의 소유자 검증 계약은 유지된다 (내부 정합성)."""
        client, db, files_dir = env
        _make_user(db, "owner")
        doc = _upload(client, "no-auth", demo_form_hwpx)
        store = DocumentStore(db, files_dir=files_dir)
        with pytest.raises(KeyError):
            store.get_preview(999999, doc["document_id"])  # 존재하지 않는 소유자

    def test_missing_document_returns_404(self, env):
        client, db, _ = env
        _, token = _make_user(db, "nobody")
        assert client.get("/api/documents/nope/preview", headers=_auth(token)).status_code == 404
