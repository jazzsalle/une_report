"""Orchestrator(M4) 단위 테스트 — FakeBackend 주입, 네트워크 호출 0회.

검증 항목 (Phase 6 T1 완료 기준):
1. 의도 3종 분기 (문서 없음 → 무조건 query 포함) + 휴리스틱 폴백
2. edit: 트레일링 콤마 깨진 JSON 복구 + 문서에 없는 id 필터링(notes 기록)
3. fill: placeholder 노드 전부에 edits 생성·청크 병합(중복 id 마지막 승리)
4. selection 필터 동작 (프롬프트 축소 + 범위 밖 id 제거)
5. LlmUnavailableError 전파 + token 전달 확인
"""
import asyncio
from typing import AsyncIterator

import pytest

from app.llm.base import LLMBackend, LlmJsonParseError, LlmUnavailableError
from app.services.orchestrator import FILL_CHUNK_SIZE, Orchestrator, TurnResult

TOKEN = "tok-test"
HISTORY = [{"role": "user", "content": "이전 발화"}]


class FakeBackend(LLMBackend):
    """고정 응답 큐를 순서대로 반환하는 LLMBackend 구현 (예외 항목은 raise)."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple[str, list | None, dict]] = []

    async def chat(self, query: str, history: list[dict] | None = None, **opts) -> str:
        self.calls.append((query, history, opts))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream(
        self, query: str, history: list[dict] | None = None, **opts
    ) -> AsyncIterator[str]:
        raise NotImplementedError("테스트에서 스트리밍은 사용하지 않음")
        yield ""  # pragma: no cover


def _run(coro):
    return asyncio.run(coro)


def _nodes(count: int, texts: list[str] | None = None) -> list[dict]:
    return [
        {"id": i, "text": texts[i] if texts else f"노드텍스트{i}", "type": "para"}
        for i in range(count)
    ]


def _turn(backend: FakeBackend, **kwargs) -> TurnResult:
    orch = Orchestrator(backend)
    return _run(orch.run_turn(token=TOKEN, message=kwargs.pop("message"),
                              history=HISTORY, **kwargs))


# ── 1. 의도 분기 ────────────────────────────────────────────────

def test_no_document_always_query():
    """문서가 없으면 LLM 분류 없이 무조건 query, edits는 빈 목록."""
    backend = FakeBackend(["일반 답변입니다."])
    result = _turn(backend, message="hwpx가 뭐야?", doc_nodes=None)
    assert result.intent == "query"
    assert result.reply == "일반 답변입니다."
    assert result.edits == []
    assert len(backend.calls) == 1  # 분류 호출 없이 chat 1회


def test_intent_edit_via_llm_classification():
    backend = FakeBackend([
        '{"intent": "edit"}',
        '{"reply": "제목을 수정했습니다", "edits": [{"id": 1, "new_text": "새 제목"}]}',
    ])
    result = _turn(backend, message="제목 바꿔줘", doc_nodes=_nodes(3))
    assert result.intent == "edit"
    assert result.reply == "제목을 수정했습니다"
    assert result.edits == [{"id": 1, "new_text": "새 제목"}]


def test_intent_fill_via_llm_classification():
    backend = FakeBackend([
        '{"intent": "fill"}',
        '{"reply": "채움", "edits": [{"id": 1, "new_text": "우리기관"},'
        ' {"id": 2, "new_text": "2026년 7월"}]}',
    ])
    placeholders = [{"id": 1, "token": "[기관명]"}, {"id": 2, "token": "YYYY년"}]
    result = _turn(backend, message="양식 채워줘", doc_nodes=_nodes(4),
                   placeholders=placeholders)
    assert result.intent == "fill"
    assert result.edits == [
        {"id": 1, "new_text": "우리기관"},
        {"id": 2, "new_text": "2026년 7월"},
    ]
    # 채움 프롬프트에 placeholder 표식이 실렸는지
    fill_prompt = backend.calls[1][0]
    assert "[기관명]" in fill_prompt


def test_intent_query_via_llm_classification():
    backend = FakeBackend(['{"intent": "query"}', "문서에는 3개 표가 있습니다."])
    result = _turn(backend, message="이 문서에 표가 몇 개야?", doc_nodes=_nodes(3))
    assert result.intent == "query"
    assert result.reply == "문서에는 3개 표가 있습니다."
    assert result.edits == []


# ── 1b. 휴리스틱 폴백 ──────────────────────────────────────────

def test_intent_fallback_heuristic_fill():
    """분류 응답이 JSON이 아니면 placeholder+작성 키워드 → fill."""
    backend = FakeBackend([
        "JSON이 아닌 그냥 텍스트 응답",
        '{"reply": "채움", "edits": [{"id": 0, "new_text": "값"}]}',
    ])
    result = _turn(backend, message="이 양식을 작성해줘", doc_nodes=_nodes(2),
                   placeholders=[{"id": 0, "token": "[기관명]"}])
    assert result.intent == "fill"


def test_intent_fallback_heuristic_edit():
    """분류 값이 비정상(intent 미허용 값)이면 수정 키워드 → edit."""
    backend = FakeBackend([
        '{"intent": "unknown-value"}',
        '{"reply": "수정 완료", "edits": []}',
    ])
    result = _turn(backend, message="두 번째 문단을 고쳐줘", doc_nodes=_nodes(3))
    assert result.intent == "edit"


def test_intent_fallback_heuristic_query():
    """분류 실패 + 키워드 없음 → query."""
    backend = FakeBackend(["???", "그냥 답변"])
    result = _turn(backend, message="이 문서 요약해줄래?", doc_nodes=_nodes(3))
    assert result.intent == "query"
    assert result.reply == "그냥 답변"


# ── 2. edit: 깨진 JSON 복구 + 무효 id 필터링 ────────────────────

def test_edit_trailing_comma_recovered_and_unknown_id_filtered():
    """트레일링 콤마 JSON 복구 + 문서에 없는 id(99) 제거·notes 기록."""
    broken = (
        '{"reply": "수정했습니다", "edits": ['
        '{"id": "p-0001", "new_text": "새 텍스트"}, '
        '{"id": 99, "new_text": "없는 노드"},], "notes": null,}'
    )
    backend = FakeBackend(['{"intent": "edit"}', broken])
    result = _turn(backend, message="수정해줘", doc_nodes=_nodes(3))
    assert result.intent == "edit"
    # "p-0001" → 1로 정수화, 99는 문서에 없어 제거
    assert result.edits == [{"id": 1, "new_text": "새 텍스트"}]
    assert result.notes is not None and "99" in result.notes
    assert len(backend.calls) == 2  # 복구 성공 → 재요청 없음


def test_edit_retry_once_then_success():
    """편집 JSON 파싱 실패 시 1회 재요청 후 성공."""
    backend = FakeBackend([
        '{"intent": "edit"}',
        "완전히 깨진 응답 텍스트",
        '{"reply": "재시도 성공", "edits": [{"id": 0, "new_text": "고침"}]}',
    ])
    result = _turn(backend, message="바꿔줘", doc_nodes=_nodes(2))
    assert result.edits == [{"id": 0, "new_text": "고침"}]
    assert len(backend.calls) == 3
    # 재요청 프롬프트에 "JSON" 경고가 앞섰는지
    assert "JSON" in backend.calls[2][0]


def test_edit_retry_fail_raises_parse_error():
    """재요청도 파싱 실패하면 LlmJsonParseError 전파."""
    backend = FakeBackend([
        '{"intent": "edit"}',
        "깨진 응답 1",
        "깨진 응답 2 역시 JSON 아님",
    ])
    orch = Orchestrator(backend)
    with pytest.raises(LlmJsonParseError):
        _run(orch.run_turn(token=TOKEN, message="바꿔줘", history=HISTORY,
                           doc_nodes=_nodes(2)))
    assert len(backend.calls) == 3


# ── 3. fill: placeholder 전부 채움 + 청크 병합 ──────────────────

def test_fill_all_placeholder_nodes_chunked_and_merged():
    """청크 크기 초과 placeholder 노드 → 청크별 호출 후 전부 병합."""
    total = FILL_CHUNK_SIZE + 5  # 35개 → 2청크 (30 + 5)
    nodes = _nodes(total)
    placeholders = [{"id": i, "token": f"[항목{i}]"} for i in range(total)]

    def chunk_response(ids):
        items = ", ".join(f'{{"id": {i}, "new_text": "값{i}"}}' for i in ids)
        return f'{{"reply": "채움", "edits": [{items}]}}'

    backend = FakeBackend([
        '{"intent": "fill"}',
        chunk_response(range(0, FILL_CHUNK_SIZE)),
        chunk_response(range(FILL_CHUNK_SIZE, total)),
    ])
    result = _turn(backend, message="양식 채워줘", doc_nodes=nodes,
                   placeholders=placeholders)
    assert result.intent == "fill"
    assert len(backend.calls) == 3  # 분류 1 + 청크 2
    assert [e["id"] for e in result.edits] == list(range(total))
    assert all(e["new_text"] == f"값{e['id']}" for e in result.edits)
    assert result.reply  # 요약 문장 존재


def test_fill_duplicate_id_last_wins():
    """같은 청크 응답 안의 중복 id는 마지막 항목이 이긴다."""
    backend = FakeBackend([
        '{"intent": "fill"}',
        '{"reply": "채움", "edits": [{"id": 1, "new_text": "먼저"},'
        ' {"id": 1, "new_text": "나중"}]}',
    ])
    result = _turn(backend, message="채워줘", doc_nodes=_nodes(3),
                   placeholders=[{"id": 1, "token": "[기관명]"}])
    assert result.edits == [{"id": 1, "new_text": "나중"}]


def test_fill_targets_only_placeholder_nodes_in_prompt():
    """fill 프롬프트에는 placeholder 포함 노드만 실린다."""
    texts = ["일반 문단 알파", "[기관명] 채움 대상", "일반 문단 베타"]
    backend = FakeBackend([
        '{"intent": "fill"}',
        '{"reply": "채움", "edits": [{"id": 1, "new_text": "우리기관 채움 대상"}]}',
    ])
    result = _turn(backend, message="작성해줘", doc_nodes=_nodes(3, texts),
                   placeholders=[{"id": 1, "token": "[기관명]"}])
    fill_prompt = backend.calls[1][0]
    assert "[기관명] 채움 대상" in fill_prompt
    assert "일반 문단 알파" not in fill_prompt
    assert result.edits == [{"id": 1, "new_text": "우리기관 채움 대상"}]


# ── 4. selection 필터 ──────────────────────────────────────────

def test_selection_filters_prompt_and_edits():
    """selection 노드만 프롬프트에 실리고, 범위 밖 id의 edits는 제거된다."""
    texts = ["알파", "베타", "감마", "델타", "엡실론"]
    backend = FakeBackend([
        '{"intent": "edit"}',
        '{"reply": "수정", "edits": [{"id": 2, "new_text": "감마-수정"},'
        ' {"id": 4, "new_text": "엡실론-수정"}]}',
    ])
    result = _turn(backend, message="선택 부분 수정해줘",
                   doc_nodes=_nodes(5, texts), selection=[2])
    edit_prompt = backend.calls[1][0]
    assert "감마" in edit_prompt
    assert "알파" not in edit_prompt and "엡실론" not in edit_prompt
    # selection 밖 id(4)는 제거되고 notes에 기록
    assert result.edits == [{"id": 2, "new_text": "감마-수정"}]
    assert result.notes is not None and "4" in result.notes


def test_selection_no_match_returns_empty_edit():
    """selection이 어떤 노드와도 안 맞으면 LLM 편집 호출 없이 빈 edits."""
    backend = FakeBackend(['{"intent": "edit"}'])
    result = _turn(backend, message="수정해줘", doc_nodes=_nodes(3),
                   selection=[99])
    assert result.intent == "edit"
    assert result.edits == []
    assert len(backend.calls) == 1  # 분류 호출만


# ── 5. 에러 전파 + token 전달 ──────────────────────────────────

def test_llm_unavailable_error_propagates():
    backend = FakeBackend([LlmUnavailableError("서버 연결 불가")])
    orch = Orchestrator(backend)
    with pytest.raises(LlmUnavailableError):
        _run(orch.run_turn(token=TOKEN, message="수정해줘", history=HISTORY,
                           doc_nodes=_nodes(2)))


def test_token_and_history_passed_to_every_call():
    backend = FakeBackend([
        '{"intent": "edit"}',
        '{"reply": "ok", "edits": []}',
    ])
    _turn(backend, message="바꿔줘", doc_nodes=_nodes(2))
    assert len(backend.calls) == 2
    for _query, history, opts in backend.calls:
        assert opts["token"] == TOKEN
        assert history == HISTORY
