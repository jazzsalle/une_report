"""POST /api/chat — SSE 대화 (M4 오케스트레이터 ↔ M5 저장소 연결).

이벤트 계약 (web/src/api.js streamChat이 이대로 소비 — 변경 금지):
  `status` → `token` → [`document_updated`] → `done`, 실패 시 `error` 후 종료.
와이어 포맷은 `event: <name>\\ndata: <json>\\n\\n`. SSE가 시작되면 HTTP
상태코드를 바꿀 수 없으므로 처리 오류도 200 + `error` 이벤트로 낸다.

한 턴의 흐름:
  rag_jwt 조회 → 세션 upsert → 문서 컨텍스트(get_nodes/get_placeholders)
  → user 메시지 저장 → Orchestrator.run_turn → edits 있으면
  apply_document_edits(새 버전) → assistant 메시지 저장 → 이벤트 송출.
"""
import json
import sqlite3
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import get_current_user, get_db
from app.api.routes_documents import get_store
from app.db.database import now_iso
from app.llm.base import (
    LLMBackend,
    LlmAuthError,
    LlmError,
    LlmJsonParseError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.services.document_store import DocumentStore, VersionConflictError
from app.services.orchestrator import Orchestrator

router = APIRouter(tags=["chat"])

# 오케스트레이터에 싣는 대화 이력 상한 (오래된 것부터 잘라냄)
HISTORY_LIMIT = 20

# document_versions.edit_summary 길이 상한 (reply 앞부분 요약)
_SUMMARY_MAX = 80

# status 이벤트의 intent별 안내 문구
_INTENT_DETAILS = {
    "edit": "문서 편집을 수행했습니다",
    "fill": "양식 채움을 수행했습니다",
    "query": "일반 질의에 응답합니다",
}


class ChatRequest(BaseModel):
    session_id: str | None = None
    document_id: str | None = None
    message: str
    selection: list[int] | None = None
    # 버전 핀: 클라이언트가 미리보기 중인 버전. 문서의 현재 버전과 다르면
    # (다른 탭에서 편집 등) LLM 호출 전에 version_conflict로 거부한다.
    base_version: int | None = None


def get_llm_backend() -> LLMBackend:
    """LLM 백엔드 의존성. 테스트는 dependency_overrides로 FakeBackend를 주입한다."""
    from app.llm.uni_rag_client import UniRagClient

    return UniRagClient()


def _sse(event: str, data: dict) -> str:
    """SSE 이벤트 1건 직렬화 — `event: <name>\\ndata: <json>\\n\\n`."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _chat_events(
    body: ChatRequest,
    db: sqlite3.Connection,
    user: sqlite3.Row,
    store: DocumentStore,
    backend: LLMBackend,
) -> AsyncIterator[str]:
    """한 턴을 처리하고 SSE 이벤트 문자열을 순서대로 낸다 (모든 오류는 error 이벤트)."""
    try:
        message = (body.message or "").strip()
        if not message:
            yield _sse("error", {"code": "bad_request", "message": "message가 비어 있습니다"})
            return

        # 1) rag_jwt — LLM 호출용 UNI RAG 토큰 (서버 보관, 없으면 재로그인 필요)
        row = db.execute(
            "SELECT rag_jwt FROM auth_tokens WHERE user_id = ?", (user["id"],)
        ).fetchone()
        rag_jwt = row["rag_jwt"] if row is not None else None
        if not rag_jwt:
            yield _sse("error", {
                "code": "auth",
                "message": "UNI RAG 인증 토큰이 없습니다. 다시 로그인해 주세요.",
            })
            return

        # 2) 세션 조회 (소유자 검증) — 없으면 새 id 발급
        session_id = body.session_id
        sess = None
        if session_id:
            sess = db.execute(
                "SELECT user_id, document_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if sess is not None and sess["user_id"] != user["id"]:
                yield _sse("error", {"code": "bad_request", "message": "세션을 찾을 수 없습니다"})
                return
        else:
            session_id = uuid.uuid4().hex

        # 3) 문서 컨텍스트 — 요청 document_id 우선, 없으면 세션에 연결된 문서
        document_id = body.document_id or (sess["document_id"] if sess is not None else None)
        doc_nodes: list[dict] | None = None
        placeholders: list[dict] | None = None
        if document_id:
            try:
                # 버전 핀 사전 검사 — LLM 호출 전에 어긋난 요청을 거부해 토큰 낭비 방지
                if body.base_version is not None:
                    current = store.get_current_version(user["id"], document_id)
                    if body.base_version != current:
                        yield _sse("error", {
                            "code": "version_conflict",
                            "message": (
                                f"문서가 다른 곳에서 수정되었습니다"
                                f" (기준 v{body.base_version}, 현재 v{current})."
                                " 미리보기를 새로고침한 뒤 다시 시도해 주세요."
                            ),
                            "current_version": current,
                        })
                        return
                doc_nodes = store.get_nodes(user["id"], document_id)
                placeholders = store.get_placeholders(user["id"], document_id)
            except KeyError:
                yield _sse("error", {"code": "bad_request", "message": "문서를 찾을 수 없습니다"})
                return

        # 4) 세션 upsert + 대화 이력 로드 + user 메시지 저장
        now = now_iso()
        if sess is None:
            db.execute(
                "INSERT INTO sessions(id, user_id, document_id, title, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, user["id"], document_id, message[:40], now, now),
            )
        else:
            db.execute(
                "UPDATE sessions SET document_id = COALESCE(?, document_id), updated_at = ?"
                " WHERE id = ?",
                (document_id, now, session_id),
            )
        history = [
            {"role": r["role"], "content": r["content"]}
            for r in reversed(db.execute(
                "SELECT role, content FROM messages"
                " WHERE session_id = ? AND role IN ('user','assistant')"
                " ORDER BY id DESC LIMIT ?",
                (session_id, HISTORY_LIMIT),
            ).fetchall())
        ]
        cur = db.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session_id, "user", message, now),
        )
        user_msg_id = cur.lastrowid
        db.commit()

        # 5) LLM 파이프라인 (M4) → 필요 시 문서 편집 적용 (M5)
        result = await Orchestrator(backend).run_turn(
            token=rag_jwt,
            message=message,
            history=history,
            doc_nodes=doc_nodes,
            placeholders=placeholders,
            selection=body.selection,
        )
        display_text = result.reply
        if result.notes:
            display_text += f"\n\n[참고] {result.notes}"

        doc_update: dict | None = None
        if result.edits and document_id:
            applied = store.apply_document_edits(
                user["id"], document_id, result.edits,
                summary=result.reply[:_SUMMARY_MAX], message_id=user_msg_id,
                expected_version=body.base_version,  # 적용 직전 재검사 (경합 방어)
            )
            doc_update = {
                "document_id": document_id,
                "version": applied["version"],
                "html": applied["html"],
                "changed_ids": applied["applied_ids"],
            }
            if applied["skipped_ids"]:
                display_text += f"\n\n[참고] 적용하지 못한 항목 id: {applied['skipped_ids']}"

        # 6) assistant 메시지 저장 (intent 포함)
        db.execute(
            "INSERT INTO messages(session_id, role, content, intent, created_at)"
            " VALUES(?,?,?,?,?)",
            (session_id, "assistant", display_text, result.intent, now_iso()),
        )
        db.commit()

        # 7) 이벤트 송출 — status → token → [document_updated] → done
        yield _sse("status", {
            "intent": result.intent,
            "detail": _INTENT_DETAILS.get(result.intent, result.intent),
        })
        yield _sse("token", {"text": display_text})  # 1차: reply 전문 1회 전송
        if doc_update is not None:
            yield _sse("document_updated", doc_update)
        yield _sse("done", {"session_id": session_id})

    except LlmAuthError:
        yield _sse("error", {
            "code": "auth",
            "message": "UNI RAG 토큰이 만료되었습니다. 다시 로그인해 주세요.",
        })
    except LlmTimeoutError as e:
        yield _sse("error", {"code": "llm_timeout", "message": str(e)})
    except LlmJsonParseError as e:
        yield _sse("error", {
            "code": "llm_unavailable",
            "message": f"LLM 응답을 해석하지 못했습니다: {e}",
        })
    except (LlmUnavailableError, LlmError) as e:
        yield _sse("error", {"code": "llm_unavailable", "message": str(e)})
    except VersionConflictError as e:
        yield _sse("error", {
            "code": "version_conflict",
            "message": str(e),
            "current_version": e.current,
        })
    except (KeyError, ValueError) as e:
        yield _sse("error", {"code": "bad_request", "message": str(e)})


@router.post("/chat")
async def chat(
    body: ChatRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(get_current_user),
    store: DocumentStore = Depends(get_store),
    backend: LLMBackend = Depends(get_llm_backend),
):
    return StreamingResponse(
        _chat_events(body, db, user, store, backend),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
