"""구조 인식 전량 재작성(C) e2e — 오류 캡처 사례 재현.

스텁 단어(title·표제목·표내용) 템플릿은 regex 표식이 없어 종전 fill이
1개 노드만 채우고 나머지를 방치했다. 재작성 fill은 문서 전체를 대상으로
새 내용을 배치하고 무관한 기존 본문을 지운다.
"""
import json
from pathlib import Path

from app.api import routes_chat
from app.core.hwpx import build_hwpx, validate_hwpx
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

# 캡처 속 "템플릿 문서"를 본뜬 스텁 템플릿 (표식 없는 일반 단어 + 기존 본문)
_STUB_SPEC = {
    "title": "title",
    "paragraphs": [
        "□ 대응 체계",
        "ㅇ 태풍 발생 전 사전 점검",
        "- 비상연락망 점검 및 갱신",
    ],
    "tables": [{"rows": [["표제목", "표제목"], ["표내용", "표내용"]]}],
}


def test_rewrite_fills_stub_template_and_clears_old_body(env, tmp_path):
    client, db, files_dir, app = env
    user_id, token = _make_user(db, "rewrite-stub")

    stub = tmp_path / "stub_template.hwpx"
    build_hwpx(_STUB_SPEC, stub)
    doc_id = _upload(client, token, stub)["document_id"]

    store = DocumentStore(db, files_dir=files_dir)
    nodes = store.get_nodes(user_id, doc_id)
    # 스텁 템플릿은 regex 표식이 없다 (종전 fill이면 대상 0 — 캡처 사례의 원인)
    assert store.get_placeholders(user_id, doc_id) == []

    by_text = {}
    for n in nodes:
        by_text.setdefault(n["text"], n["id"])
    title_id = by_text["title"]
    old_body_id = by_text["ㅇ 태풍 발생 전 사전 점검"]
    header_id = by_text["표제목"]

    rewrite_edits = [
        {"id": title_id, "new_text": "호우 위기 대응 매뉴얼"},
        {"id": old_body_id, "new_text": ""},                    # 옛 본문 삭제
        {"id": header_id, "new_text": "위기 단계"},              # 머리글 대체
    ]
    backend = FakeBackend([
        '{"intent": "fill"}',
        json.dumps({"reply": "재작성 완료", "edits": rewrite_edits}, ensure_ascii=False),
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    ev = _chat(client, token, {
        "document_id": doc_id,
        "message": "호우 위기 대응 매뉴얼 내용이야. 이 내용을 템플릿에 그대로 채워줘",
    })
    assert ev["status"]["intent"] == "fill"
    upd = ev["document_updated"]
    assert upd["version"] == 1

    # fill 프롬프트에 표식 없는 스텁 노드들이 전부 실렸는가 (전량 재작성 대상)
    fill_prompt = backend.calls[1][0]
    assert "title" in fill_prompt
    assert "표제목" in fill_prompt
    assert "태풍 발생 전 사전 점검" in fill_prompt

    # export 재파싱: 새 내용 반영 + 옛 본문 소거
    exported = _export_hwpx(client, token, doc_id, tmp_path / "rewritten.hwpx")
    assert validate_hwpx(exported).ok
    texts = _node_texts(exported, tmp_path)
    assert texts[title_id] == "호우 위기 대응 매뉴얼"
    assert texts[old_body_id] == ""          # 구조만 남고 내용은 삭제됨
    assert texts[header_id] == "위기 단계"


def test_fill_progress_status_events_stream_order(env, tmp_path):
    """다청크 fill에서 진행 status가 먼저 흐르고 최종 이벤트 계약은 불변이다.

    이벤트 순서: status*(진행, 청크 수만큼) → status(최종) → token
    → document_updated → done.
    """
    from tests.test_chat_api import parse_sse

    client, db, files_dir, app = env
    _user_id, token = _make_user(db, "progress-user")

    big = tmp_path / "big_form.hwpx"
    build_hwpx({"title": "제목", "paragraphs": [f"문단 {i}" for i in range(40)]}, big)
    doc_id = _upload(client, token, big)["document_id"]

    backend = FakeBackend([
        '{"intent": "fill"}',                                        # 병합: fill 위임
        '{"reply": "1"}\n{"id": 1, "new_text": "값1"}\n{"notes": ""}',  # 청크 1 (JSONL)
        '{"reply": "2"}\n{"notes": ""}',                              # 청크 2
    ])
    app.dependency_overrides[routes_chat.get_llm_backend] = lambda: backend

    resp = client.post("/api/chat", headers=_auth(token), json={
        "document_id": doc_id, "message": "호우 매뉴얼 내용으로 작성해줘",
    })
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    names = [n for n, _ in events]

    # 진행 status: 청크 수(2)만큼, progress 필드 포함
    progress = [d for n, d in events if n == "status" and "progress" in d]
    assert [(p["progress"]["current"], p["progress"]["total"]) for p in progress] == [
        (1, 2), (2, 2),
    ]
    assert all("진행 중" in p["detail"] for p in progress)

    # 최종 이벤트 계약 불변: 마지막 4개가 status → token → document_updated → done
    assert names[-4:] == ["status", "token", "document_updated", "done"]
    final_status = events[len(names) - 4][1]
    assert final_status["intent"] == "fill"
    assert "progress" not in final_status
