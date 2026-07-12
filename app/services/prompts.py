"""M4 chat-orchestrator 프롬프트 상수·빌더 (한국어).

모든 프롬프트는 UNI RAG `/chat/`(비스트리밍)의 `query`로 들어가며,
구조화 응답이 필요한 곳(의도 분류·편집·채움)은 "다른 설명 없이 JSON만
출력"을 명시한다. 응답은 `parse_llm_json`(M3-4)으로 복구·파싱하므로
코드펜스·트레일링 콤마 정도의 깨짐은 허용된다.
"""

# ── 응답 JSON 형식 예시 (프롬프트에 그대로 삽입) ────────────────

INTENT_JSON_EXAMPLE = '{"intent": "edit"}'

# LLM 편집 응답 계약 (DESIGN.md M4-2): reply/edits/notes
# NOTE: LLM은 예시의 분량을 강하게 모방하므로 new_text 예시는 실제 기대
# 분량 수준(완결된 공문서 문장)으로 유지한다 — 짧은 예시는 짧은 출력을 유발.
EDIT_JSON_EXAMPLE = (
    '{"reply": "요청하신 항목을 수정했습니다.", '
    '"edits": [{"id": 12, "new_text": "본 계획은 재난 발생 시 신속한 초동 대응과 '
    '피해 최소화를 목적으로 하며, 관계 기관과의 협조 체계를 포함한다."}], '
    '"notes": "적용하지 못한 항목이 있으면 그 사유 (없으면 생략)"}'
)

# 분류+응답 병합 계약 (M4 개선): intent를 포함해 한 번에 받는다.
# intent=fill이면 edits는 무시되고 별도 채움 파이프라인이 실행된다.
TURN_JSON_EXAMPLE = (
    '{"intent": "edit", '
    '"reply": "요청하신 항목을 수정했습니다.", '
    '"edits": [{"id": 12, "new_text": "본 계획은 재난 발생 시 신속한 초동 대응과 '
    '피해 최소화를 목적으로 하며, 관계 기관과의 협조 체계를 포함한다."}], '
    '"notes": "적용하지 못한 항목이 있으면 그 사유 (없으면 생략)"}'
)

# JSON 파싱 실패 시 1회 재요청에 앞세우는 경고문
RETRY_PREFIX = (
    "직전 응답을 JSON으로 파싱하지 못했다. 이번에는 코드펜스·설명·사고 과정 없이 "
    "유효한 JSON 객체 하나만 출력하라.\n\n"
)


# ── 공통 포매터 ─────────────────────────────────────────────────

def format_nodes(nodes: list[dict]) -> str:
    """문서 노드 목록을 'id [유형] 텍스트' 한 줄 형식으로 나열한다."""
    lines = []
    for n in nodes:
        node_type = n.get("type") or "para"
        text = (n.get("text") or "").replace("\n", " ")
        lines.append(f"{n['id']} [{node_type}] {text}")
    return "\n".join(lines)


def format_placeholders(placeholders: list[dict]) -> str:
    """placeholder 목록을 'id: 표식' 한 줄 형식으로 나열한다."""
    return "\n".join(f"{p['id']}: {p.get('token', '')}" for p in placeholders)


# ── 의도 분류 (M4-1) ────────────────────────────────────────────

def build_intent_prompt(message: str, *, has_placeholders: bool = False) -> str:
    """사용자 발화 의도를 edit/fill/query 중 하나로 분류하는 프롬프트."""
    ph_state = (
        "이 문서에는 채움 대상 placeholder([기관명], YYYY년 등)가 있다."
        if has_placeholders
        else "이 문서에 별도 placeholder는 감지되지 않았다."
    )
    return (
        "당신은 hwpx 문서 편집 도구의 의도 분류기다. 사용자 발화를 아래 세 가지 중 "
        "정확히 하나로 분류하라.\n\n"
        '- "edit": 문서의 특정 내용을 수정·변경·삭제·교체하라는 지시\n'
        '- "fill": 양식의 placeholder를 채우거나 문서 초안·내용을 작성해 달라는 요청\n'
        '- "query": 그 외 일반 질문이나 문서 내용에 대한 질문 (문서를 바꾸지 않음)\n\n'
        f"{ph_state}\n\n"
        f"사용자 발화:\n{message}\n\n"
        "다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n"
        f"{INTENT_JSON_EXAMPLE}"
    )


# ── 분류+응답 병합 (M4 개선 — 턴당 LLM 1회) ─────────────────────

def build_turn_prompt(
    message: str, nodes: list[dict], placeholders: list[dict]
) -> str:
    """의도 분류와 본 작업(편집/질의 답변)을 한 번에 요청하는 병합 프롬프트.

    응답 계약: {"intent", "reply", "edits", "notes"}.
    - edit  → edits에 수정 항목을 담는다 (이 호출로 종결)
    - query → reply에 답변을 담고 edits는 빈 배열 (이 호출로 종결)
    - fill  → intent만 쓰이고 별도 채움 파이프라인이 실행되므로 edits는 비워도 된다
    JSON 파싱 실패 시 오케스트레이터가 기존 분리 경로(분류→작업)로 폴백한다.
    """
    ph_section = (
        "채움 대상 placeholder 목록 (형식: 노드 id: 표식):\n"
        f"{format_placeholders(placeholders)}\n\n"
        if placeholders
        else ""
    )
    return (
        "당신은 hwpx 문서 편집 도구다. 사용자 발화의 의도를 분류하고, 의도에 따른 "
        "결과까지 한 번에 만들어라.\n\n"
        "의도 분류 기준:\n"
        '- "edit": 문서의 특정 내용을 수정·변경·삭제·교체하라는 지시 → 해당 노드의 '
        "새 텍스트를 edits에 담아라.\n"
        '- "fill": 양식의 placeholder를 채우거나 문서 초안·내용을 작성해 달라는 요청 '
        "→ intent만 정확히 답하고 edits는 빈 배열로 두어라 (별도 파이프라인이 처리한다).\n"
        '- "query": 그 외 일반 질문이나 문서 내용에 대한 질문 (문서를 바꾸지 않음) '
        "→ reply에 답변을 쓰고 edits는 빈 배열로 두어라.\n\n"
        "문서 노드 목록 (형식: id [유형] 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"{ph_section}"
        f"사용자 발화:\n{message}\n\n"
        "규칙:\n"
        "- 지시와 무관한 노드는 edits에 넣지 마라.\n"
        "- id는 위 목록에 있는 값만 그대로 사용하라 (새 id를 만들지 마라).\n"
        "- new_text는 해당 노드의 전체 텍스트를 대체할 완성된 문장으로 써라.\n"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{TURN_JSON_EXAMPLE}"
    )


# ── 편집 지시 (M4-2·M4-4) ───────────────────────────────────────

def build_edit_prompt(message: str, nodes: list[dict]) -> str:
    """문서 노드 목록 + 편집 지시 → edits JSON을 요청하는 프롬프트."""
    return (
        "당신은 hwpx 문서 편집 도우미다. 아래 문서 노드 목록에서 사용자 지시에 "
        "해당하는 노드만 골라 새 텍스트를 만들어라.\n\n"
        "문서 노드 목록 (형식: id [유형] 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"사용자 지시:\n{message}\n\n"
        "규칙:\n"
        "- 지시와 무관한 노드는 edits에 넣지 마라.\n"
        "- id는 위 목록에 있는 값만 그대로 사용하라 (새 id를 만들지 마라).\n"
        "- new_text는 해당 노드의 전체 텍스트를 대체할 완성된 문장으로 써라.\n"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{EDIT_JSON_EXAMPLE}"
    )


# ── 템플릿 채움 (M4-3) ──────────────────────────────────────────

def build_fill_prompt(
    message: str, nodes: list[dict], placeholders: list[dict]
) -> str:
    """placeholder 포함 노드 청크 → 채움 edits JSON을 요청하는 프롬프트."""
    ph_section = (
        "채움 대상 placeholder 목록 (형식: 노드 id: 표식):\n"
        f"{format_placeholders(placeholders)}\n\n"
        if placeholders
        else ""
    )
    return (
        "당신은 hwpx 양식 문서를 채우는 도우미다. 아래 노드들의 placeholder"
        "([기관명], <담당자>, YYYY년, ○○ 등)를 사용자 제공 내용에 맞는 실제 값으로 "
        "바꾼 새 텍스트를 만들어라.\n\n"
        "문서 노드 목록 (형식: id [유형] 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"{ph_section}"
        f"사용자 요청·제공 내용:\n{message}\n\n"
        "규칙:\n"
        "- 위 노드 목록의 모든 id에 대해 placeholder를 해소한 edits 항목을 만들어라.\n"
        "- new_text는 노드의 전체 텍스트를 대체하므로, placeholder가 아닌 부분은 "
        "원문을 유지한 채 표식만 바꿔라.\n"
        "- 분량: 서술형 문단([유형] para)의 new_text는 공문서 문체로 3~5문장의 "
        "완결된 서술로 작성하고, 표 셀([유형] cell)은 1~2문장(또는 항목명·수치 등 "
        "셀 성격에 맞는 값)으로 간결하게 작성하라. 한두 구절로 얼버무리지 마라.\n"
        "- 사용자 내용만으로 알 수 없는 값은 문맥상 자연스러운 초안으로 채우고 "
        "notes에 확인 필요 항목으로 적어라.\n"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{EDIT_JSON_EXAMPLE}"
    )


# ── JSON 재요청 (M3-4 연계) ────────────────────────────────────

def build_retry_prompt(original_prompt: str) -> str:
    """JSON 파싱 실패 후 1회 재요청용 프롬프트 (원 프롬프트에 경고 접두)."""
    return RETRY_PREFIX + original_prompt
