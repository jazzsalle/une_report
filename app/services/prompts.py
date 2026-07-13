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
    """문서 노드를 본문 줄 + 표별 그리드로 직렬화한다 (B2).

    - 본문: "id [유형] 텍스트" (종전 형식 유지)
    - 표 셀: "[표 N]" 헤더 아래 "(행,열) id: 텍스트" — 같은 행·열의 라벨이
      인접해 나열되므로 빈 셀이 어느 라벨의 값 칸인지 LLM에게 드러난다.
      빈 셀은 "(빈 칸)"으로 표기한다. (셀 노드는 파서 순번 규칙상 같은 표
      단위로 연속되므로 table_idx 변화 지점에 헤더만 끼워 넣으면 된다)
    """
    lines: list[str] = []
    current_table = None
    for n in nodes:
        table_idx = n.get("table_idx")
        if table_idx is None:
            current_table = None
            node_type = n.get("type") or "para"
            text = (n.get("text") or "").replace("\n", " ")
            lines.append(f"{n['id']} [{node_type}] {text}")
            continue
        if table_idx != current_table:
            current_table = table_idx
            lines.append(f"[표 {table_idx}]")
        text = (n.get("text") or "").replace("\n", " ").strip() or "(빈 칸)"
        span = ""
        if (n.get("row_span") or 1) > 1 or (n.get("col_span") or 1) > 1:
            span = f" 병합{n.get('row_span') or 1}x{n.get('col_span') or 1}"
        lines.append(f"({n.get('row')},{n.get('col')}){span} {n['id']}: {text}")
    return "\n".join(lines)


def format_placeholders(placeholders: list[dict]) -> str:
    """채움 대상 목록을 'id [표식|지시]: 내용' 형식으로 나열한다.

    kind="guide"(파란 기울임체 작성 가이드)는 [지시]로 구분해, 문구 자체가
    작성 지시임을 LLM에게 알린다.
    """
    lines = []
    for p in placeholders:
        label = "지시" if p.get("kind") == "guide" else "표식"
        lines.append(f"{p['id']} [{label}]: {p.get('token', '')}")
    return "\n".join(lines)


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
        '- "fill": 양식·템플릿을 채우거나, 제공한 내용으로 문서를 작성·재작성해 '
        "달라는 요청\n"
        '- "query": 그 외 일반 질문이나 문서 내용에 대한 질문 (문서를 바꾸지 않음)\n\n'
        f"{ph_state}\n\n"
        f"사용자 발화:\n{message}\n\n"
        "다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n"
        f"{INTENT_JSON_EXAMPLE}"
    )


# ── 분류+응답 병합 (M4 개선 — 턴당 LLM 1회) ─────────────────────

def build_turn_prompt(
    message: str, nodes: list[dict], placeholders: list[dict],
    numbering_rule: str = "",
) -> str:
    """의도 분류와 본 작업(편집/질의 답변)을 한 번에 요청하는 병합 프롬프트.

    응답 계약: {"intent", "reply", "edits", "notes"}.
    - edit  → edits에 수정 항목을 담는다 (이 호출로 종결)
    - query → reply에 답변을 담고 edits는 빈 배열 (이 호출로 종결)
    - fill  → intent만 쓰이고 별도 채움 파이프라인이 실행되므로 edits는 비워도 된다
    JSON 파싱 실패 시 오케스트레이터가 기존 분리 경로(분류→작업)로 폴백한다.
    """
    ph_section = (
        "채움 대상 목록 (형식: 노드 id [표식|지시]: 내용):\n"
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
        '- "fill": 양식·템플릿을 채우거나 제공한 내용으로 문서를 작성·재작성해 '
        "달라는 요청 → intent만 정확히 답하고 edits는 빈 배열로 두어라 "
        "(별도 파이프라인이 처리한다).\n"
        '- "query": 그 외 일반 질문이나 문서 내용에 대한 질문 (문서를 바꾸지 않음) '
        "→ reply에 답변을 쓰고 edits는 빈 배열로 두어라.\n\n"
        "문서 노드 목록 (본문: id [유형] 텍스트 / 표: (행,열) id: 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"{ph_section}"
        f"사용자 발화:\n{message}\n\n"
        "규칙:\n"
        "- 지시와 무관한 노드는 edits에 넣지 마라.\n"
        "- id는 위 목록에 있는 값만 그대로 사용하라 (새 id를 만들지 마라).\n"
        "- new_text는 해당 노드의 전체 텍스트를 대체할 완성된 문장으로 써라.\n"
        f"{numbering_rule + chr(10) if numbering_rule else ''}"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{TURN_JSON_EXAMPLE}"
    )


# ── 편집 지시 (M4-2·M4-4) ───────────────────────────────────────

def build_edit_prompt(
    message: str, nodes: list[dict], numbering_rule: str = ""
) -> str:
    """문서 노드 목록 + 편집 지시 → edits JSON을 요청하는 프롬프트."""
    return (
        "당신은 hwpx 문서 편집 도우미다. 아래 문서 노드 목록에서 사용자 지시에 "
        "해당하는 노드만 골라 새 텍스트를 만들어라.\n\n"
        "문서 노드 목록 (본문: id [유형] 텍스트 / 표: (행,열) id: 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"사용자 지시:\n{message}\n\n"
        "규칙:\n"
        "- 지시와 무관한 노드는 edits에 넣지 마라.\n"
        "- id는 위 목록에 있는 값만 그대로 사용하라 (새 id를 만들지 마라).\n"
        "- new_text는 해당 노드의 전체 텍스트를 대체할 완성된 문장으로 써라.\n"
        f"{numbering_rule + chr(10) if numbering_rule else ''}"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{EDIT_JSON_EXAMPLE}"
    )


# ── 템플릿 채움 (M4-3) ──────────────────────────────────────────

def build_fill_prompt(
    message: str, nodes: list[dict], placeholders: list[dict],
    numbering_rule: str = "",
) -> str:
    """노드 청크 → 구조 인식 재작성 edits JSON을 요청하는 프롬프트 (C).

    기존 텍스트를 구조 힌트(제목 자리·헤딩 기호·표 머리글·라벨·표식)로
    제공하고, 사용자 내용을 구조에 맞게 배치해 전량 재작성하게 한다.
    무관한 기존 본문은 빈 문자열로 지우게 한다.
    """
    ph_section = (
        "채움 단서 목록 (형식: 노드 id [표식|지시]: 내용):\n"
        f"{format_placeholders(placeholders)}\n\n"
        if placeholders
        else ""
    )
    return (
        "당신은 hwpx 템플릿에 내용을 채워 문서를 완성하는 도우미다. 아래 노드 "
        "목록은 템플릿의 **구조**다. 기존 텍스트는 구조 힌트 — 제목 자리, 헤딩의 "
        "번호·개요 기호(□·ㅇ·- 등), 표 머리글, 라벨 셀, placeholder([기관명], "
        "YYYY 등), 안내 문구 — 로만 취급하고, 사용자 제공 내용을 이 구조에 맞게 "
        "배치해 문서 전체를 다시 채워라.\n\n"
        "문서 노드 목록 (본문: id [유형] 텍스트 / 표: (행,열) id: 텍스트):\n"
        f"{format_nodes(nodes)}\n\n"
        f"{ph_section}"
        f"사용자 요청·제공 내용:\n{message}\n\n"
        "규칙:\n"
        "- 구조에 맞게 배치하라: 제목 자리(title, 문서 상단의 표 등)에는 문서 "
        "제목을, 헤딩에는 절 제목을(기존 번호·개요 기호 형식은 유지), 표 머리글 "
        "셀에는 새 내용에 맞는 열 제목을, 값 칸·(빈 칸) 셀에는 같은 행·열의 "
        "머리글에 해당하는 값을 써라.\n"
        "- 기존 텍스트가 예시·안내·이전 문서의 잔여 내용이라 새 내용과 무관하고 "
        '구조 역할도 없다면 "new_text": ""로 지워라. 템플릿의 옛 문장을 결과물에 '
        "남기지 마라.\n"
        "- [지시]가 붙은 노드는 그 지시문(작성 가이드)이 요구하는 내용·분량·형식에 "
        "맞춰 실제 내용을 작성하고, 지시문 자체는 new_text에 남기지 마라.\n"
        "- placeholder([기관명], <담당자>, YYYY년, ○○ 등)는 실제 값으로 바꿔라.\n"
        "- 원문을 그대로 유지할 노드만 edits에서 생략하라 (기본은 전량 재작성).\n"
        f"{numbering_rule + chr(10) if numbering_rule else ''}"
        "- 분량: 서술형 문단([유형] para)의 new_text는 공문서 문체로 3~5문장의 "
        "완결된 서술로 작성하고, 표 셀은 1~2문장(또는 항목명·수치 등 셀 성격에 "
        "맞는 값)으로 간결하게 작성하라. 한두 구절로 얼버무리지 마라.\n"
        "- 사용자 내용만으로 알 수 없는 값은 문맥상 자연스러운 초안으로 채우고 "
        "notes에 확인 필요 항목으로 적어라.\n"
        "- 다른 설명 없이 아래 형식의 JSON 객체 하나만 출력하라.\n\n"
        f"{EDIT_JSON_EXAMPLE}"
    )


# ── JSON 재요청 (M3-4 연계) ────────────────────────────────────

def build_retry_prompt(original_prompt: str) -> str:
    """JSON 파싱 실패 후 1회 재요청용 프롬프트 (원 프롬프트에 경고 접두)."""
    return RETRY_PREFIX + original_prompt
