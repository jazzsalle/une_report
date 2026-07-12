"""버전 핀(A2) 테스트 — base_version 불일치 시 version_conflict, 일치 시 정상 적용.

test_e2e_scenarios.py의 픽스처·헬퍼를 재사용해 실제 라우터 스택으로 검증한다.
"""
import json

import pytest

from app.api import routes_chat
from app.services.document_store import DocumentStore, VersionConflictError
from tests.test_chat_api import parse_sse
from tests.test_e2e_scenarios import (  # noqa: F401 — env는 픽스처로 재사용
    _auth,
    _chat,
    _make_user,
    _upload,
    env,
)
from tests.test_orchestrator import FakeBackend


def _setup(env, demo_form_hwpx, account: str):
    client, db, files_dir, app = env
    user_id, token = _make_user(db, account)
    doc_id = _upload(client, token, demo_form_hwpx)["document_id"]
    store = DocumentStore(db, files_dir=files_dir)
    return client, app, store, token, user_id, doc_id


def test_stale_base_version_rejected_before_llm(env, demo_form_hwpx):
    """어긋난 base_version은 LLM 호출 전에 version_conflict로 거부된다."""
    client, app, store, token, user_id, doc_id = _setup(env, demo_form_hwpx, "verpin-stale")
    backend = FakeBackend(["호출되면 안 됨"])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    resp = client.post("/api/chat", headers=_auth(token), json={
        "document_id": doc_id, "message": "제목 바꿔줘", "base_version": 3,
    })
    events = parse_sse(resp.text)
    assert events[0][0] == "error"
    data = events[0][1]
    assert data["code"] == "version_conflict"
    assert data["current_version"] == 0
    assert backend.calls == []  # LLM 호출 전에 거부 → 토큰 낭비 없음


def test_matching_base_version_applies_edit(env, demo_form_hwpx):
    """현재 버전과 일치하는 base_version이면 종전대로 편집이 적용된다."""
    client, app, store, token, user_id, doc_id = _setup(env, demo_form_hwpx, "verpin-ok")
    target = store.get_nodes(user_id, doc_id)[0]["id"]
    backend = FakeBackend([
        json.dumps({
            "intent": "edit", "reply": "수정",
            "edits": [{"id": target, "new_text": "버전 핀 확인 문장"}],
        }, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {
        "document_id": doc_id, "message": "수정해줘", "base_version": 0,
    })
    assert ev["document_updated"]["version"] == 1


def test_missing_base_version_keeps_legacy_behavior(env, demo_form_hwpx):
    """base_version 없는 요청은 종전 동작(항상 최신 버전에 적용)을 유지한다."""
    client, app, store, token, user_id, doc_id = _setup(env, demo_form_hwpx, "verpin-legacy")
    target = store.get_nodes(user_id, doc_id)[0]["id"]
    backend = FakeBackend([
        json.dumps({
            "intent": "edit", "reply": "수정",
            "edits": [{"id": target, "new_text": "레거시 호환 문장"}],
        }, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {"document_id": doc_id, "message": "수정해줘"})
    assert ev["document_updated"]["version"] == 1


def test_store_expected_version_mismatch_raises(env, demo_form_hwpx):
    """저장소 계층: expected_version 불일치 시 VersionConflictError."""
    client, app, store, token, user_id, doc_id = _setup(env, demo_form_hwpx, "verpin-store")
    with pytest.raises(VersionConflictError) as ei:
        store.apply_document_edits(
            user_id, doc_id, [{"id": 0, "new_text": "적용되면 안 됨"}],
            expected_version=7,
        )
    assert ei.value.expected == 7
    assert ei.value.current == 0
    # 버전이 생성되지 않았어야 한다
    assert store.get_current_version(user_id, doc_id) == 0
