"""시나리오 C·D end-to-end 테스트 (DESIGN.md §3) — FakeBackend, 네트워크 0회.

test_e2e_scenarios.py의 픽스처·헬퍼(env/_upload/_chat/_export_hwpx/_node_texts)를
그대로 재사용해 실제 라우터 스택(documents + chat + sessions)으로 검증한다.

- 시나리오 C: 부분 선택 편집 — selection에 담긴 전역 id만 편집 대상이 되고,
  LLM이 범위 밖 id를 섞어 보내도 필터링되어 changed_ids ⊆ selection 유지.
  export 후 재파싱으로 선택 노드만 변경·비선택 노드 원문 보존을 확인한다.
- 시나리오 D: 일반 질의 — intent=query면 document_updated 이벤트가 없고
  문서 버전이 [0] 그대로. 이어서 같은 세션의 edit 발화로 버전 1이 생성되며,
  이전 질의·답변이 대화 이력(history)으로 LLM에 전달되는지 확인한다.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.hwpx import validate_hwpx
from app.services.document_store import DocumentStore
from tests.test_e2e_scenarios import (  # noqa: F401 — env는 픽스처로 재사용
    _auth,
    _chat,
    _export_hwpx,
    _make_user,
    _node_texts,
    _upload,
    env,
)
from tests.test_orchestrator import FakeBackend

from app.api import routes_chat


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _setup_doc(env, demo_form_hwpx, account: str):
    """업로드까지 마친 공통 셋업 — (client, app, store, token, user_id, doc_id, 원문 dict)."""
    client, db, files_dir, app = env
    user_id, token = _make_user(db, account)
    doc = _upload(client, token, demo_form_hwpx)
    doc_id = doc["document_id"]
    store = DocumentStore(db, files_dir=files_dir)
    original = {n["id"]: n["text"] for n in store.get_nodes(user_id, doc_id)}
    return client, app, store, token, user_id, doc_id, original


def _id_of(original: dict[int, str], needle: str) -> int:
    """원문 텍스트에 needle이 들어 있는 노드의 전역 id."""
    return next(gid for gid, text in original.items() if needle in text)


def _versions(client: TestClient, token: str, doc_id: str) -> list[int]:
    resp = client.get(f"/api/documents/{doc_id}/versions", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return [v["version"] for v in resp.json()]


# ---------------------------------------------------------------------------
# 시나리오 C — 부분 선택 편집 (selection → 선택 노드만 수정, 나머지 보존)
# ---------------------------------------------------------------------------

def test_scenario_c_selection_edit_only_changes_selected_nodes(
    env, demo_form_hwpx, tmp_path
):
    client, app, store, token, user_id, doc_id, original = _setup_doc(
        env, demo_form_hwpx, "scenario-c"
    )

    # 미리보기에서 두 노드를 드래그 선택했다고 가정 (전역 data-id 목록)
    intro_id = _id_of(original, "본 문서는 단위 테스트용")
    cycle_id = _id_of(original, "연 1회")
    selection = [intro_id, cycle_id]

    formal_intro = "본 문서는 단위 테스트를 위하여 마련된 임시 양식입니다."
    formal_cycle = "연 1회 정기적으로 수립합니다."
    backend = FakeBackend([
        # 병합 응답: 분류+편집이 한 호출에 담긴다
        json.dumps({
            "intent": "edit",
            "reply": "선택하신 부분을 격식 있는 문장으로 다듬었습니다",
            "edits": [
                {"id": intro_id, "new_text": formal_intro},
                {"id": cycle_id, "new_text": formal_cycle},
            ],
        }, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {
        "document_id": doc_id,
        "message": "이 부분을 더 격식 있는 문장으로 바꿔줘",
        "selection": selection,
    })
    assert ev["status"]["intent"] == "edit"
    upd = ev["document_updated"]
    assert upd["version"] == 1
    assert set(upd["changed_ids"]) <= set(selection)  # 선택 범위의 부분집합
    assert sorted(upd["changed_ids"]) == sorted(selection)
    assert formal_intro in upd["html"]

    # 병합 프롬프트에는 선택 노드만 실린다 (비선택 노드 텍스트 부재)
    edit_prompt = backend.calls[0][0]
    assert "본 문서는 단위 테스트용" in edit_prompt
    assert "[주소 입력]" not in edit_prompt

    # export → 유효한 hwpx + 재파싱: 선택 노드만 변경, 비선택 노드 전원 원문 보존
    exported = _export_hwpx(client, token, doc_id, tmp_path / "scenario_c.hwpx")
    validation = validate_hwpx(exported)
    assert validation.ok, validation.errors

    texts = _node_texts(exported, tmp_path)
    assert texts[intro_id] == formal_intro
    assert texts[cycle_id] == formal_cycle
    untouched = {gid: t for gid, t in original.items() if gid not in selection}
    assert {gid: texts[gid] for gid in untouched} == untouched

    # 여유분 스모크: 같은 문서를 docx로도 export — 200 + zip 시그니처(PK)
    resp = client.post(
        f"/api/documents/{doc_id}/export", headers=_auth(token),
        json={"format": "docx"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:2] == b"PK"


def test_scenario_c_out_of_selection_edits_are_filtered(
    env, demo_form_hwpx, tmp_path
):
    """LLM이 selection 밖 id를 섞어 보내도 필터링되어 changed_ids ⊆ selection."""
    client, app, store, token, user_id, doc_id, original = _setup_doc(
        env, demo_form_hwpx, "scenario-c2"
    )

    target_id = _id_of(original, "본 문서는 단위 테스트용")
    outside_id = _id_of(original, "담당자")  # 문서에 존재하지만 선택 밖
    selection = [target_id]

    backend = FakeBackend([
        json.dumps({
            "intent": "edit",
            "reply": "수정했습니다",
            "edits": [
                {"id": target_id, "new_text": "격식을 갖춘 개요 문장입니다."},
                {"id": outside_id, "new_text": "선택 밖 노드 — 적용되면 안 됨"},
            ],
        }, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {
        "document_id": doc_id,
        "message": "이 부분을 더 격식 있는 문장으로 바꿔줘",
        "selection": selection,
    })
    assert ev["status"]["intent"] == "edit"
    upd = ev["document_updated"]
    assert upd["changed_ids"] == [target_id]
    assert set(upd["changed_ids"]) <= set(selection)
    # 필터링 사실이 notes로 사용자 표시 텍스트에 남는다
    assert str(outside_id) in ev["token"]["text"]

    # export 재파싱: 선택 밖 노드는 원문 그대로
    exported = _export_hwpx(client, token, doc_id, tmp_path / "scenario_c2.hwpx")
    assert validate_hwpx(exported).ok
    texts = _node_texts(exported, tmp_path)
    assert texts[target_id] == "격식을 갖춘 개요 문장입니다."
    assert texts[outside_id] == original[outside_id]


# ---------------------------------------------------------------------------
# 시나리오 D — 일반 질의 (문서 불변) → 이어서 같은 세션에서 문서 반영
# ---------------------------------------------------------------------------

def test_scenario_d_query_leaves_document_unchanged_then_edit_applies(
    env, demo_form_hwpx, tmp_path
):
    client, app, store, token, user_id, doc_id, original = _setup_doc(
        env, demo_form_hwpx, "scenario-d"
    )

    answer = "대피 기준은 진도 4 이상 지진 또는 홍수주의보 발령 시 즉시 대피입니다."
    backend = FakeBackend([
        # 병합 응답: 분류+답변이 한 호출에 담긴다
        json.dumps({"intent": "query", "reply": answer}, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    # 1) 일반 질의 — document_updated 부재 + 문서 버전 [0] 그대로
    question = "이 문서에서 대피 기준이 뭐야?"
    ev = _chat(client, token, {"document_id": doc_id, "message": question})
    assert ev["status"]["intent"] == "query"
    assert "document_updated" not in ev
    assert ev["token"]["text"] == answer
    assert _versions(client, token, doc_id) == [0]
    session_id = ev["done"]["session_id"]

    # 2) 같은 세션에서 답변 내용을 문서에 반영 (edit) → version 1
    first_para_id = _id_of(original, "본 문서는 단위 테스트용")
    filled = "대피 기준: 진도 4 이상 지진 또는 홍수주의보 발령 시 즉시 대피한다."
    backend.responses.extend([
        json.dumps({
            "intent": "edit",
            "reply": "답변 내용을 첫 문단에 반영했습니다",
            "edits": [{"id": first_para_id, "new_text": filled}],
        }, ensure_ascii=False),
    ])
    ev2 = _chat(client, token, {
        "session_id": session_id,
        "document_id": doc_id,
        "message": "방금 답변 내용으로 첫 문단을 채워줘",
    })
    assert ev2["status"]["intent"] == "edit"
    assert ev2["document_updated"]["version"] == 1
    assert ev2["document_updated"]["changed_ids"] == [first_para_id]
    assert _versions(client, token, doc_id) == [0, 1]

    # 3) 두 번째 턴의 LLM 호출(병합)에 이전 질의·답변이 history로 전달됐다
    assert len(backend.calls) == 2  # 병합 경로: 질의 1회 + 편집 1회
    for _query, history, opts in backend.calls[1:]:
        assert {"role": "user", "content": question} in history
        assert {"role": "assistant", "content": answer} in history
        assert opts["token"] == ""  # 로그인 삭제 — 빈 토큰 (계약 유지용)

    # 4) export 재파싱 — 반영된 텍스트 존재 + 나머지 노드 원문 보존
    exported = _export_hwpx(client, token, doc_id, tmp_path / "scenario_d.hwpx")
    assert validate_hwpx(exported).ok
    texts = _node_texts(exported, tmp_path)
    assert texts[first_para_id] == filled
    untouched = {gid: t for gid, t in original.items() if gid != first_para_id}
    assert {gid: texts[gid] for gid in untouched} == untouched
