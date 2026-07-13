"""numbering.py 테스트: 항목 기호 감지·프롬프트 규칙 생성·프롬프트 반영."""
import pytest

from app.services import prompts
from app.services.numbering import (
    STANDARD_LEVELS,
    build_numbering_rule,
    detect_item_scheme,
)


def _nodes(*texts: str) -> list[dict]:
    return [{"id": i, "text": t, "type": "para"} for i, t in enumerate(texts)]


class TestDetectItemScheme:
    def test_detects_special_symbols_in_first_appearance_order(self):
        """실양식(코로나 대비계획) 패턴: □ 헤딩 → ㅇ(이응 대용) → * 각주."""
        scheme = detect_item_scheme(_nodes(
            "□ 검토배경",
            "ㅇ 오미크론 정점 이후 방역상황이 안정적으로 관리되고 있으나",
            "① 코로나 19 범정부 대응·지원 체계 운영",
            "- 세부 추진 과제",
        ))
        assert scheme == ["□", "○", "①", "-"]

    def test_detects_standard_levels(self):
        scheme = detect_item_scheme(_nodes(
            "1. 추진 배경",
            "가. 계획 수립 근거",
            "1) 관련 법령",
            "가) 세부 조항",
            "(1) 감염병 예방법",
            "(가) 제4조",
            "① 국가의 책무",
            "㉮ 세부 항목",
        ))
        assert scheme == STANDARD_LEVELS

    def test_ignores_dates_decimals_and_plain_text(self):
        scheme = detect_item_scheme(_nodes(
            "2026. 7. 13. 기준 현황",   # 연도(4자리)는 항목 번호가 아님
            "일반 본문 문장이다.",
            "ㆍ 소항목",
        ))
        assert scheme == ["ㆍ"]

    def test_hyphen_requires_trailing_space(self):
        scheme = detect_item_scheme(_nodes("-30.0%", "- 감소 추세"))
        assert scheme == ["-"]

    def test_empty_nodes(self):
        assert detect_item_scheme([]) == []
        assert detect_item_scheme(_nodes("", "  ")) == []


class TestBuildNumberingRule:
    def test_with_detected_scheme(self):
        rule = build_numbering_rule(["□", "○"])
        assert "□ → ○" in rule            # 감지된 체계 명시
        assert "1. → 가. → 1)" in rule    # 하위 확장용 표준 순서
        assert "가나다순" in rule
        assert rule.startswith("- ")       # 규칙 목록 항목 형식

    def test_without_scheme_falls_back_to_standard(self):
        rule = build_numbering_rule([])
        assert "감지된 항목 기호가 없으므로" in rule
        assert "1. → 가. → 1)" in rule


class TestPromptIntegration:
    def test_fill_prompt_contains_rule(self):
        nodes = _nodes("□ 검토배경", "ㅇ 본문")
        rule = build_numbering_rule(detect_item_scheme(nodes))
        prompt = prompts.build_fill_prompt("호우 대비 계획 작성", nodes, [], numbering_rule=rule)
        assert "항목 번호·기호 부여" in prompt
        assert "□ → ○" in prompt

    def test_edit_and_turn_prompts_contain_rule(self):
        nodes = _nodes("1. 개요")
        rule = build_numbering_rule(detect_item_scheme(nodes))
        assert "항목 번호·기호 부여" in prompts.build_edit_prompt("수정", nodes, numbering_rule=rule)
        assert "항목 번호·기호 부여" in prompts.build_turn_prompt("수정", nodes, [], numbering_rule=rule)

    def test_prompts_unchanged_without_rule(self):
        nodes = _nodes("본문")
        assert "항목 번호·기호 부여" not in prompts.build_fill_prompt("작성", nodes, [])
        assert "항목 번호·기호 부여" not in prompts.build_edit_prompt("수정", nodes)


class TestOrchestratorWiring:
    def test_fill_pipeline_prompt_carries_document_scheme(self):
        """fill 청크 프롬프트에 문서 전체에서 감지한 항목 체계가 실린다."""
        import asyncio

        from tests.test_orchestrator import FakeBackend
        from app.services.orchestrator import Orchestrator

        backend = FakeBackend([
            '{"intent": "fill", "reply": "", "edits": []}',   # 병합 경로: 분류만
            '{"reply": "채움", "edits": [{"id": 0, "new_text": "□ 새 제목"}]}',
        ])
        orch = Orchestrator(backend)
        result = asyncio.run(orch.run_turn(
            token="tok", message="호우 대비 계획으로 작성해줘", history=[],
            doc_nodes=[
                {"id": 0, "text": "□ 검토배경", "type": "para"},
                {"id": 1, "text": "ㅇ 배경 설명 문장", "type": "para"},
            ],
            placeholders=[],
        ))
        assert result.intent == "fill"
        fill_prompt = backend.calls[1][0]
        assert "항목 번호·기호 부여" in fill_prompt
        assert "□ → ○" in fill_prompt  # 문서에서 감지된 체계
