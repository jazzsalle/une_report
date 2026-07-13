"""대화 오케스트레이터 (M4) — 의도 분류·편집·채움 파이프라인.

사용자 발화 + 문서 컨텍스트 → LLM(M3) 호출 → 검증된 `TurnResult` 반환.
DB·파일·FastAPI를 만지지 않는 순수 서비스 계층으로, `LLMBackend`만
생성자 주입으로 받는다 (API 계층 T4가 이 계약을 그대로 소비한다).

파이프라인 (DESIGN.md M4 + 병합 개선):
- 병합 경로(기본): 문서 있음 → 분류+응답을 한 프롬프트로 요청
  (`build_turn_prompt`, 계약 {"intent","reply","edits","notes"}).
  edit·query는 이 1회 호출로 종결, fill은 분류 결과만 쓰고 청크
  파이프라인으로 위임. JSON 실패·비정상 intent면 아래 분리 경로로 폴백.
- M4-1 의도 분류(폴백 경로): 문서 없음 → 무조건 query. 문서 있음 →
  LLM 1회 분류, JSON 실패·비정상 값이면 키워드 휴리스틱 폴백.
- M4-2·M4-4 편집: (selection 필터된) 노드 목록을 프롬프트에 실어
  `{"reply","edits","notes"}` JSON 수신 → normalize_edit_id 정수화 →
  문서에 없는 id 제거(notes 기록). 파싱 실패 시 1회 재요청, 재실패면
  LlmJsonParseError 전파.
- M4-3 채움 (C — 구조 인식 전량 재작성): 기본 대상 = 문서의 모든 노드
  (기존 텍스트는 구조 힌트로 쓰고 사용자 내용으로 전량 재배치, 무관한
  본문은 빈 문자열로 삭제). FILL_NODE_LIMIT 초과 대형 문서는 표식·가이드
  중심(B3)으로 축소. 표 경계를 우선하는 청크로 나눠 청크별 호출 →
  edits 병합 (중복 id는 마지막 승리).
- 프롬프트 규모 제어 (B3): edit·query 프롬프트는 selection이 없으면
  텍스트 없는 노드를 제외한 축소 목록을 쓴다.
- query: backend.chat 응답을 그대로 reply로.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

from app import config
from app.core.hwpx.edits import normalize_edit_id
from app.llm.base import LLMBackend, LlmJsonParseError
from app.llm.json_parser import parse_llm_json, parse_llm_json_or_partial
from app.llm.jsonl_stream import JsonlTurn, collect_jsonl_turn
from app.services import numbering, prompts

# 노드 한 줄 직렬화 시 텍스트 외 고정 오버헤드 추정치 (id·좌표·유형 표기)
_NODE_LINE_OVERHEAD = 32

# 의도 분류 결과로 허용되는 값
INTENTS = ("edit", "fill", "query")

# 채움 파이프라인의 청크 크기 (노드 수 기준, "30노드 내외")
FILL_CHUNK_SIZE = 30

# 의도 분류 휴리스틱 폴백 키워드 (fill을 edit보다 먼저 검사)
_FILL_KEYWORDS = ("작성", "채워", "초안", "다시 작성", "재작성")
_EDIT_KEYWORDS = ("수정", "바꿔", "변경", "고쳐")

async def _single_delta(raw: str):
    """텍스트 하나를 1회 yield하는 async 이터레이터 (비스트림 응답의 JSONL 해석용)."""
    yield raw


# 진행 통지 콜백: {"phase","current","total","detail"} — routes_chat이 SSE status로 중계
ProgressCallback = Callable[[dict], Awaitable[None]]


# LLM 응답이 max_tokens로 잘려 부분만 복구·반영했을 때의 사용자 안내
_TRUNCATED_NOTE = (
    "LLM 응답이 길이 제한으로 잘려 일부 항목만 반영되었습니다. "
    "반영되지 않은 부분은 미리보기에서 범위를 좁혀 선택한 뒤 다시 요청해 주세요."
)


@dataclass
class TurnResult:
    """한 턴의 처리 결과 (API 계층이 그대로 직렬화해 응답한다)."""

    intent: str                 # "edit" | "fill" | "query"
    reply: str                  # 사용자 표시 텍스트
    edits: list[dict]           # [{"id": int, "new_text": str}] — query면 []
    notes: str | None = None    # 적용 불가 항목 사유 등 (없으면 None)


class Orchestrator:
    """의도 분류 → 편집/채움/질의 파이프라인 실행기."""

    def __init__(self, backend: LLMBackend):
        self.backend = backend
        self._on_progress: ProgressCallback | None = None

    # ── 공개 API ───────────────────────────────────────────────

    async def run_turn(
        self,
        *,
        token: str,
        message: str,
        history: list[dict],                    # [{"role","content"}]
        doc_nodes: list[dict] | None = None,    # [{"id","text","type"}]
        placeholders: list[dict] | None = None,  # [{"id","token"}]
        selection: list[int] | None = None,
        on_progress: ProgressCallback | None = None,  # 진행 통지 (SSE status 중계용)
    ) -> TurnResult:
        """사용자 발화 한 턴을 처리해 TurnResult를 반환한다."""
        self._on_progress = on_progress
        if not doc_nodes:
            # 문서가 없으면 편집·채움이 성립하지 않으므로 무조건 query
            return await self._run_query(token, message, history)

        nodes = self._filter_selection(doc_nodes, selection)
        # edit·query 프롬프트용 축소 목록 (B3 — 빈 노드 제외 + 문자 예산)
        prompt_nodes, omitted = self._prompt_nodes(nodes, placeholders or [], selection)

        # 병합 경로: 분류+응답을 1회 호출로 (실패 시 None → 분리 경로 폴백)
        combined = await self._try_combined_turn(
            token, message, history, nodes, prompt_nodes, placeholders or []
        )
        if combined is not None:
            return self._note_omitted(combined, omitted)

        intent = await self._classify_intent(token, message, history, placeholders)
        if intent == "edit":
            result = await self._run_edit(token, message, history, prompt_nodes)
            return self._note_omitted(result, omitted)
        if intent == "fill":
            return await self._run_fill(token, message, history, nodes, placeholders or [])
        return await self._run_query(token, message, history)

    @staticmethod
    def _prompt_nodes(
        nodes: list[dict], placeholders: list[dict], selection: list[int] | None
    ) -> tuple[list[dict], int]:
        """edit·query 프롬프트에 실을 노드 (B3 — 프롬프트 규모 제어).

        반환: (노드 목록, 예산 초과로 생략된 노드 수).
        - selection이 있으면 사용자가 빈 셀을 직접 지목했을 수 있으므로 전부
          유지한다 (선택 범위는 작다는 전제).
        - 없으면 텍스트 없는 노드를 제외하되 placeholder/가이드 노드는 남기고,
          누적 직렬화 길이가 PROMPT_CHAR_BUDGET을 넘으면 이후 노드를 생략한다
          (UNI RAG는 쿼리 약 48k자 초과 시 HTTP 500 — config 주석 실측 참조).
        """
        if selection:
            return nodes, 0
        keep_ids: set[int] = set()
        for p in placeholders:
            sid = Orchestrator._safe_id(p.get("id"))
            if sid is not None:
                keep_ids.add(sid)
        candidates = [
            n for n in nodes
            if (n.get("text") or "").strip() or int(n["id"]) in keep_ids
        ]
        budget = config.PROMPT_CHAR_BUDGET
        used = 0
        kept: list[dict] = []
        for n in candidates:
            used += len(n.get("text") or "") + _NODE_LINE_OVERHEAD
            if used > budget and kept:
                break
            kept.append(n)
        return kept, len(candidates) - len(kept)

    @staticmethod
    def _note_omitted(result: TurnResult, omitted: int) -> TurnResult:
        """프롬프트 예산으로 생략된 노드가 있으면 notes에 안내를 덧붙인다."""
        if omitted <= 0:
            return result
        note = (
            f"문서가 길어 뒤쪽 노드 {omitted}개는 이번 요청에서 제외되었습니다. "
            "해당 부분을 편집하려면 미리보기에서 그 부분을 선택한 뒤 다시 요청하세요."
        )
        result.notes = f"{result.notes}; {note}" if result.notes else note
        return result

    # ── 분류+응답 병합 경로 (턴당 LLM 1회) ─────────────────────

    async def _try_combined_turn(
        self,
        token: str,
        message: str,
        history: list[dict],
        nodes: list[dict],
        prompt_nodes: list[dict],
        placeholders: list[dict],
    ) -> TurnResult | None:
        """병합 프롬프트 1회로 분류+응답을 시도한다. 폴백이 필요하면 None.

        - edit  → edits 검증 후 즉시 TurnResult (1회 종결)
        - query → reply 그대로 TurnResult (1회 종결)
        - fill  → 분류 결과만 사용하고 기존 청크 채움 파이프라인 실행
          (프롬프트·edits 검증은 축소 목록 prompt_nodes 기준, fill 위임은
          빈 셀을 포함해야 하므로 전체 nodes 기준)
        - JSON 파싱 실패·비정상 intent·빈 query 답변 → None (분리 경로 폴백)
        LLM 연결 오류(LlmError 계열)는 종전과 동일하게 상위로 전파한다.
        """
        if not prompt_nodes:
            return None  # 편집 대상 없음 — 분리 경로의 안내 메시지에 맡긴다
        # placeholder도 노드 범위(selection 필터 후)로 좁힌다 — 프롬프트에
        # 선택 밖 노드의 표식이 새어 들어가지 않게 (edits 검증과 동일 기준)
        node_ids = {int(n["id"]) for n in prompt_nodes}
        prompt_placeholders = [
            p for p in placeholders if self._safe_id(p.get("id")) in node_ids
        ]
        # 항목 체계는 전체 노드에서 감지 (축소 목록 밖 기호도 반영)
        rule = numbering.build_numbering_rule(numbering.detect_item_scheme(nodes))
        prompt = prompts.build_turn_prompt(
            message, prompt_nodes, prompt_placeholders, numbering_rule=rule
        )
        try:
            # 병합 경로는 재요청 없이 1회만 (실패해도 분리 경로가 재시도한다)
            data, truncated = await self._chat_turn_stream(
                token, prompt, history, retry=False
            )
        except LlmJsonParseError:
            return None
        intent = str(data.get("intent", "")).strip().lower()
        if intent not in INTENTS:
            return None
        if intent == "fill":
            return await self._run_fill(token, message, history, nodes, placeholders)
        if intent == "query":
            reply = self._extract_reply(data, default="")
            if not reply:
                return None  # 답변 없는 query는 분리 경로에서 재시도
            return TurnResult(
                intent="query", reply=reply, edits=[],
                notes=_TRUNCATED_NOTE if truncated else None,
            )
        edits, local_notes = self._extract_edits(data, node_ids)
        if truncated:
            local_notes.append(_TRUNCATED_NOTE)
        reply = self._extract_reply(data, default=f"{len(edits)}개 항목을 수정했습니다.")
        return TurnResult(
            intent="edit",
            reply=reply,
            edits=edits,
            notes=self._merge_notes(data, local_notes),
        )

    # ── 의도 분류 (M4-1) ───────────────────────────────────────

    async def _classify_intent(
        self,
        token: str,
        message: str,
        history: list[dict],
        placeholders: list[dict] | None,
    ) -> str:
        """LLM 1회 호출로 의도를 분류한다. JSON 실패·비정상 값은 휴리스틱 폴백."""
        prompt = prompts.build_intent_prompt(
            message, has_placeholders=bool(placeholders)
        )
        raw = await self.backend.chat(prompt, history, token=token)
        intent = ""
        try:
            data = parse_llm_json(raw)
            if isinstance(data, dict):
                intent = str(data.get("intent", "")).strip().lower()
        except LlmJsonParseError:
            pass  # 분류 실패는 치명적이지 않으므로 휴리스틱으로 폴백
        if intent in INTENTS:
            return intent
        return self._heuristic_intent(message, placeholders)

    @staticmethod
    def _heuristic_intent(message: str, placeholders: list[dict] | None) -> str:
        """키워드 기반 폴백 분류."""
        if placeholders and any(kw in message for kw in _FILL_KEYWORDS):
            return "fill"
        if any(kw in message for kw in _EDIT_KEYWORDS):
            return "edit"
        return "query"

    # ── 편집 파이프라인 (M4-2·M4-4) ────────────────────────────

    async def _run_edit(
        self, token: str, message: str, history: list[dict], nodes: list[dict]
    ) -> TurnResult:
        if not nodes:
            return TurnResult(
                intent="edit",
                reply="편집할 대상 노드가 없습니다. 선택 영역을 확인해 주세요.",
                edits=[],
                notes="selection에 해당하는 문서 노드가 없음",
            )
        rule = numbering.build_numbering_rule(numbering.detect_item_scheme(nodes))
        prompt = prompts.build_edit_prompt(message, nodes, numbering_rule=rule)
        data, truncated = await self._chat_turn_stream(token, prompt, history)
        valid_ids = {int(n["id"]) for n in nodes}
        edits, local_notes = self._extract_edits(data, valid_ids)
        if truncated:
            local_notes.append(_TRUNCATED_NOTE)
        reply = self._extract_reply(data, default=f"{len(edits)}개 항목을 수정했습니다.")
        return TurnResult(
            intent="edit",
            reply=reply,
            edits=edits,
            notes=self._merge_notes(data, local_notes),
        )

    # ── 채움 파이프라인 (M4-3) ─────────────────────────────────

    async def _run_fill(
        self,
        token: str,
        message: str,
        history: list[dict],
        nodes: list[dict],
        placeholders: list[dict],
    ) -> TurnResult:
        # 대상 선정 (C — 구조 인식 전량 재작성): 어떤 템플릿이든(스텁 단어,
        # 이미 채워진 과거 문서 포함) 문서 전체를 대상으로 삼아 구조만 남기고
        # 사용자 내용으로 다시 채운다. 단 FILL_NODE_LIMIT을 넘는 대형 문서는
        # 호출 폭주를 막기 위해 표식·가이드 중심(B3 방식)으로 축소한다.
        notes_all: list[str] = []
        ph_ids: set[int] = set()
        for p in placeholders:
            try:
                ph_ids.add(normalize_edit_id(p["id"]))
            except (ValueError, KeyError):
                continue
        if len(nodes) <= config.FILL_NODE_LIMIT:
            targets = list(nodes)  # 전량 재작성 (빈 셀 포함)
        elif ph_ids:
            ph_tables = {
                n["table_idx"] for n in nodes
                if n.get("table_idx") is not None and int(n["id"]) in ph_ids
            }
            targets = [
                n for n in nodes
                if int(n["id"]) in ph_ids
                or (
                    n.get("table_idx") in ph_tables
                    and not (n.get("text") or "").strip()
                )
            ]
            notes_all.append(
                f"문서가 커서(노드 {len(nodes)}개) 표식·가이드 중심으로 채웠습니다. "
                "다른 부분의 전면 재작성이 필요하면 미리보기에서 선택 후 요청하세요."
            )
        else:
            # 표식조차 없는 대형 문서: 앞에서부터 상한까지만 (호출 폭주 방지)
            targets = nodes[:config.FILL_NODE_LIMIT]
            notes_all.append(
                f"문서가 커서(노드 {len(nodes)}개) 앞쪽 {len(targets)}개 노드만 "
                "채웠습니다. 나머지는 미리보기에서 선택 후 요청하세요."
            )

        # 항목 체계는 문서 전체에서 1회 감지해 모든 청크에 동일하게 적용
        # (청크에는 기호가 안 보여도 문서의 체계를 이어받게 한다)
        rule = numbering.build_numbering_rule(numbering.detect_item_scheme(nodes))
        merged: dict[int, str] = {}
        chunks = self._fill_chunks(targets, FILL_CHUNK_SIZE)
        failed_chunks = 0
        last_parse_err: LlmJsonParseError | None = None
        for i, chunk in enumerate(chunks):
            if self._on_progress is not None:
                await self._on_progress({
                    "phase": "fill_chunk",
                    "current": i + 1,
                    "total": len(chunks),
                    "detail": f"양식 채움 진행 중 ({i + 1}/{len(chunks)} 구간)",
                })
            chunk_ids = {int(n["id"]) for n in chunk}
            chunk_ph = [
                p for p in placeholders
                if self._safe_id(p.get("id")) in chunk_ids
            ]
            prompt = prompts.build_fill_prompt(
                message, chunk, chunk_ph, numbering_rule=rule
            )
            try:
                data, truncated = await self._chat_turn_stream(token, prompt, history)
            except LlmJsonParseError as e:
                # 청크 격리: 이 청크만 포기하고 나머지는 계속 (부분 성공 보존).
                # 연결·타임아웃·인증 등 다른 LlmError는 즉시 전파(기존 계약).
                failed_chunks += 1
                last_parse_err = e
                lo, hi = chunk[0]["id"], chunk[-1]["id"]
                notes_all.append(
                    f"{i + 1}번째 구간(노드 {lo}~{hi})의 응답을 해석하지 못해 "
                    "채우지 못했습니다. 해당 부분은 다시 요청해 주세요."
                )
                continue
            if truncated and _TRUNCATED_NOTE not in notes_all:
                notes_all.append(_TRUNCATED_NOTE)
            edits, local_notes = self._extract_edits(data, chunk_ids)
            for e in edits:
                merged[e["id"]] = e["new_text"]  # 중복 id는 마지막 승리
            notes_all.extend(local_notes)
            llm_notes = data.get("notes")
            if isinstance(llm_notes, str) and llm_notes.strip():
                notes_all.append(llm_notes.strip())

        if chunks and failed_chunks == len(chunks):
            # 전 청크 실패 — 부분 성공이 전혀 없으므로 기존 예외 경로 유지
            raise last_parse_err

        edits_out = [
            {"id": gid, "new_text": text} for gid, text in sorted(merged.items())
        ]
        reply = (
            f"양식 채움을 완료했습니다. {len(edits_out)}개 항목에 내용을 생성했습니다."
            if edits_out
            else "채울 수 있는 항목을 찾지 못했습니다. 제공 내용을 확인해 주세요."
        )
        if edits_out and failed_chunks:
            reply += " (일부 구간은 채우지 못했습니다 — 참고 사항을 확인해 주세요.)"
        notes = "; ".join(notes_all) if notes_all else None
        return TurnResult(intent="fill", reply=reply, edits=edits_out, notes=notes)

    # ── 일반 질의 ──────────────────────────────────────────────

    async def _run_query(
        self, token: str, message: str, history: list[dict]
    ) -> TurnResult:
        reply = await self.backend.chat(message, history, token=token)
        return TurnResult(intent="query", reply=reply, edits=[])

    # ── 내부 헬퍼 ──────────────────────────────────────────────

    async def _chat_turn_stream(
        self, token: str, prompt: str, history: list[dict], *, retry: bool = True
    ) -> tuple[dict, bool]:
        """LLM 호출 → 응답 해석. 반환: (data dict, truncated 여부).

        해석 사다리 (응답 잘림 내성):
        1. backend.chat_stream()으로 JSONL 수신 — 완성된 줄까지는 잘려도 산다.
           chat_stream 미구현 백엔드(NotImplementedError)는 chat() 폴백.
        2. JSONL 줄이 하나도 안 잡히면(계약 위반: 통 JSON·산문) 수신 전문을
           기존 parse_llm_json 사다리로 해석, 실패 시 잘림 부분 복구.
           (잘림의 원인은 대부분 max_tokens라 재요청보다 복구가 우선 —
            복구분이 쓸 만하면 재요청 생략)
        3. 그래도 못 살리면 retry=True일 때 JSONL 경고 접두로 1회 재요청 후
           같은 사다리 재적용. 최종 실패는 LlmJsonParseError.

        연결·타임아웃·인증 오류(LlmError 비-파싱 계열)는 그대로 전파한다.
        """
        result = await self._request_turn(token, prompt, history)
        if result is None and retry:
            raw = await self.backend.chat(
                prompts.build_retry_prompt_jsonl(prompt), history, token=token
            )
            result = await self._interpret_response(raw)
        if result is not None:
            return result
        raise LlmJsonParseError("LLM 응답을 해석하지 못했습니다 (JSONL·JSON·잘림 복구 모두 실패)")

    async def _request_turn(
        self, token: str, prompt: str, history: list[dict]
    ) -> tuple[dict, bool] | None:
        """1차 요청: 스트리밍 JSONL 우선, 미구현 백엔드는 비스트림 폴백."""
        try:
            stream = self.backend.chat_stream(prompt, history, token=token)
            turn = await collect_jsonl_turn(stream)
        except NotImplementedError:
            raw = await self.backend.chat(prompt, history, token=token)
            return await self._interpret_response(raw)
        if turn.parsed_lines > 0:
            data = self._shape_turn(turn)
            if not turn.truncated or self._salvage_usable(data):
                return data, turn.truncated
            return None  # 잘렸는데 살린 게 없음 → 재요청이 낫다
        # JSONL 계약 위반 응답 — 수신 전문을 통 JSON 사다리로
        return self._parse_or_salvage(turn.raw)

    async def _interpret_response(self, raw: str) -> tuple[dict, bool] | None:
        """비스트림 텍스트 응답을 JSONL 우선 → 통 JSON/잘림 복구 순으로 해석."""
        turn = await collect_jsonl_turn(_single_delta(raw))
        if turn.parsed_lines > 0:
            data = self._shape_turn(turn)
            if not turn.truncated or self._salvage_usable(data):
                return data, turn.truncated
            return None
        return self._parse_or_salvage(raw)

    @staticmethod
    def _shape_turn(turn: JsonlTurn) -> dict:
        """JsonlTurn을 기존 응답 dict 모양으로 변환한다 (None 필드는 제외 —
        intent=None이 str(None)="none"으로 새는 사고 방지)."""
        data = {
            "intent": turn.intent,
            "reply": turn.reply,
            "edits": turn.edits,
            "notes": turn.notes,
        }
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def _parse_or_salvage(cls, raw: str) -> tuple[dict, bool] | None:
        """응답을 (dict, truncated)로 해석한다. 채택 불가면 None(재요청 신호)."""
        try:
            data, truncated = parse_llm_json_or_partial(raw)
        except LlmJsonParseError:
            return None
        if not isinstance(data, dict):
            return None
        if truncated and not cls._salvage_usable(data):
            return None  # 살린 게 없으면 재요청이 낫다
        return data, truncated

    @staticmethod
    def _salvage_usable(data: dict) -> bool:
        """잘림 복구본이 쓸 만한가 — 완성 edit ≥ 1 또는 reply 텍스트 존재."""
        edits = data.get("edits")
        if isinstance(edits, list) and any(
            isinstance(e, dict) and "id" in e and "new_text" in e for e in edits
        ):
            return True
        reply = data.get("reply")
        return isinstance(reply, str) and bool(reply.strip())

    @staticmethod
    def _extract_edits(
        data: dict, valid_ids: set[int]
    ) -> tuple[list[dict], list[str]]:
        """응답 dict에서 edits를 검증·정수화한다. 무효 항목은 notes로 수집."""
        edits_out: list[dict] = []
        notes: list[str] = []
        raw_edits = data.get("edits") or []
        if not isinstance(raw_edits, list):
            return [], [f"edits가 배열이 아니어서 무시함: {type(raw_edits).__name__}"]
        for item in raw_edits:
            if not isinstance(item, dict) or "id" not in item:
                notes.append(f"형식이 잘못된 edit 항목 제외: {item!r}")
                continue
            try:
                gid = normalize_edit_id(item["id"])
            except ValueError:
                notes.append(f"해석할 수 없는 id 제외: {item['id']!r}")
                continue
            if gid not in valid_ids:
                notes.append(f"문서에 없는 id 제외: {gid}")
                continue
            new_text = item.get("new_text")
            edits_out.append({
                "id": gid,
                "new_text": "" if new_text is None else str(new_text),
            })
        return edits_out, notes

    @staticmethod
    def _extract_reply(data: dict, *, default: str) -> str:
        reply = data.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()
        return default

    @staticmethod
    def _merge_notes(data: dict, local_notes: list[str]) -> str | None:
        """LLM notes + 검증 과정 notes를 합친다. 없으면 None."""
        parts: list[str] = []
        llm_notes = data.get("notes")
        if isinstance(llm_notes, str) and llm_notes.strip():
            parts.append(llm_notes.strip())
        parts.extend(local_notes)
        return "; ".join(parts) if parts else None

    @staticmethod
    def _filter_selection(
        doc_nodes: list[dict], selection: list[int] | None
    ) -> list[dict]:
        """selection(data-id 목록)이 있으면 해당 노드만 남긴다 (M4-4)."""
        if not selection:
            return list(doc_nodes)
        sel = {int(s) for s in selection}
        return [n for n in doc_nodes if int(n["id"]) in sel]

    @staticmethod
    def _safe_id(value) -> int | None:
        try:
            return normalize_edit_id(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _chunks(items: list[dict], size: int) -> list[list[dict]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

    @classmethod
    def _fill_chunks(cls, targets: list[dict], size: int) -> list[list[dict]]:
        """표 불분할을 보장하는 청킹 (B3).

        문서 순서를 유지한 채 같은 표의 셀들을 그룹으로 묶은 뒤, 그룹들을
        size 상한까지 greedy하게 한 청크에 담는다. size를 넘는 큰 표만
        행 순서를 유지한 채 내부 분할한다. 즉 표가 프롬프트 중간에서 끊겨
        라벨-값 문맥이 사라지는 일은 없고, 작은 양식은 종전처럼 소수의
        청크(호출)로 처리된다.
        """
        groups: list[tuple[object, list[dict]]] = []
        for n in targets:
            key = n.get("table_idx") if n.get("table_idx") is not None else "body"
            if groups and groups[-1][0] == key:
                groups[-1][1].append(n)
            else:
                groups.append((key, [n]))

        chunks: list[list[dict]] = []
        current: list[dict] = []
        for _key, items in groups:
            if len(items) > size:
                # 상한을 넘는 큰 표: 단독으로 내부 분할 (다른 그룹과 섞지 않음)
                if current:
                    chunks.append(current)
                    current = []
                chunks.extend(cls._chunks(items, size))
                continue
            if len(current) + len(items) > size:
                chunks.append(current)
                current = []
            current.extend(items)
        if current:
            chunks.append(current)
        return chunks
