"""edits JSONL 스트림 수집기 (응답 잘림 내성).

LLM에게 "한 줄에 완결된 JSON 객체 하나"(JSON Lines) 계약으로 응답을 받고,
chat_stream() 델타를 줄 단위로 확정 파싱한다. 응답이 max_tokens로 중간에
잘려도 **완성된 줄까지는 반영**되므로, 단일 JSON 객체 계약(전부 아니면
전무)보다 잘림 내성이 크다.

줄 분류 규칙:
- {"id": ..., "new_text": ...}  → edit 줄
- {"intent"|"reply": ...}       → 헤더 줄 (edits 배열이 실려 있으면 흡수 —
  LLM이 계약을 어기고 한 줄짜리 통 JSON을 내도 그대로 동작한다)
- {"notes": ...}                → notes 줄
- 빈 줄·코드펜스(```)·<think> 블록 줄은 건너뛴다.

EOF에 남은 미완성 꼬리 줄은 json_parser._complete_truncated로 구제를
시도하되, edit 줄은 id·new_text가 모두 완결된 경우에만 채택한다
(잘린 new_text를 빈 값으로 날조해 노드를 지우는 사고 방지).

parsed_lines == 0이면 LLM이 JSONL 계약을 아예 무시한 것(단일 객체
pretty-print·산문 등)이므로, 호출부는 raw 전체를 기존 parse_llm_json
사다리로 폴백시켜야 한다.
"""
import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from app.llm.json_parser import _complete_truncated


@dataclass
class JsonlTurn:
    """JSONL 스트림 수집 결과. 필드 의미는 모듈 docstring 참조."""

    intent: str | None = None
    reply: str | None = None
    edits: list[dict] = field(default_factory=list)
    notes: str | None = None
    raw: str = ""            # 수신 전문 (parsed_lines==0일 때 폴백 파싱용)
    parsed_lines: int = 0    # JSON으로 해석된 줄 수 (0 = 계약 위반 신호)
    truncated: bool = False  # 미완성 꼬리 줄을 살리지 못하고 버림


async def collect_jsonl_turn(
    chunks: AsyncIterator[str],
    *,
    on_edit: Callable[[dict], None] | None = None,
) -> JsonlTurn:
    """chat_stream 델타를 JSONL로 수집한다. LlmError 계열은 그대로 전파."""
    turn = JsonlTurn()
    buf = ""
    in_think = False

    def _add_edit(item: dict) -> None:
        turn.edits.append(item)
        if on_edit is not None:
            on_edit(item)

    def _classify(obj: object) -> None:
        if not isinstance(obj, dict):
            return
        if "id" in obj:
            _add_edit(obj)
            return
        if turn.intent is None and isinstance(obj.get("intent"), str):
            turn.intent = obj["intent"]
        if turn.reply is None and isinstance(obj.get("reply"), str):
            turn.reply = obj["reply"]
        if isinstance(obj.get("edits"), list):
            # 한 줄짜리 통 JSON 호환: 헤더 줄에 실린 edits 배열 흡수
            for e in obj["edits"]:
                if isinstance(e, dict) and "id" in e:
                    _add_edit(e)
        if isinstance(obj.get("notes"), str):
            turn.notes = obj["notes"]

    def _process_line(line: str) -> None:
        nonlocal in_think
        stripped = line.strip()
        if in_think:
            if "</think>" in stripped:
                in_think = False
            return
        if not stripped or stripped.startswith("```"):
            return
        if stripped.startswith("<think>"):
            if "</think>" not in stripped:
                in_think = True
            return
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return
        turn.parsed_lines += 1
        _classify(obj)

    async for delta in chunks:
        turn.raw += delta
        buf += delta
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            _process_line(line)

    # EOF: 남은 꼬리 (개행 없이 끝난 마지막 줄)
    tail = buf.strip()
    if not tail or in_think or tail.startswith("```"):
        return turn
    try:
        obj = json.loads(tail)
    except json.JSONDecodeError:
        obj = None
    if obj is not None:
        turn.parsed_lines += 1
        _classify(obj)
        return turn  # 정확히 줄 경계(개행만 없음)에서 끝남 — 잘림 아님
    if turn.parsed_lines == 0:
        return turn  # 계약 위반(통 JSON 등) — 호출부의 raw 폴백에 맡긴다
    # 꼬리 줄이 잘렸다 = 이후 내용 유실 → 구제 성공 여부와 무관하게 truncated
    turn.truncated = True
    # 잘린 꼬리 줄 구제 시도 — edit 줄은 id·new_text 완결 시에만 채택
    # (잘린 new_text를 빈 값으로 날조해 노드를 지우는 사고 방지)
    repaired = _complete_truncated(tail)
    if repaired is not None:
        try:
            robj = json.loads(repaired)
        except json.JSONDecodeError:
            robj = None
        if isinstance(robj, dict):
            if "id" in robj:
                if "new_text" in robj:
                    _add_edit(robj)
            elif robj.get("reply") or robj.get("notes"):
                _classify(robj)
    return turn
