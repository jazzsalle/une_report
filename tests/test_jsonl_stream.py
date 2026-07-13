"""collect_jsonl_turn(JSONL 스트림 수집기) 단위 테스트 — 네트워크 0회."""
import asyncio

import pytest

from app.llm.jsonl_stream import collect_jsonl_turn

FULL = (
    '{"reply": "수정했습니다"}\n'
    '{"id": 1, "new_text": "첫 항목"}\n'
    '{"id": 2, "new_text": "둘째 항목"}\n'
    '{"notes": "확인 필요 없음"}'
)


def _collect(deltas, **kw):
    async def _gen():
        for d in deltas:
            yield d

    return asyncio.run(collect_jsonl_turn(_gen(), **kw))


class TestNormalStream:
    @pytest.mark.parametrize("splitter", [
        lambda s: [s],                                    # 통짜 1델타
        lambda s: list(s),                                # 1문자씩
        lambda s: [s[i:i + 7] for i in range(0, len(s), 7)],   # 줄 경계 무시 7자
        lambda s: s.splitlines(keepends=True),            # 줄 단위
    ])
    def test_split_invariant(self, splitter):
        """델타 분할 지점과 무관하게 같은 결과가 나와야 한다."""
        turn = _collect(splitter(FULL))
        assert turn.reply == "수정했습니다"
        assert turn.edits == [
            {"id": 1, "new_text": "첫 항목"},
            {"id": 2, "new_text": "둘째 항목"},
        ]
        assert turn.notes == "확인 필요 없음"
        assert turn.parsed_lines == 4
        assert turn.truncated is False
        assert turn.raw == FULL

    def test_notes_omitted_clean_end_not_truncated(self):
        """notes 줄 없이 줄 경계에서 끝나면 잘림으로 치지 않는다."""
        text = '{"reply": "완료"}\n{"id": 3, "new_text": "값"}\n'
        turn = _collect([text])
        assert turn.edits == [{"id": 3, "new_text": "값"}]
        assert turn.truncated is False

    def test_intent_header_recognized(self):
        text = '{"intent": "edit", "reply": "고쳤습니다"}\n{"id": 0, "new_text": "새 값"}\n'
        turn = _collect([text])
        assert turn.intent == "edit"
        assert turn.reply == "고쳤습니다"
        assert turn.edits == [{"id": 0, "new_text": "새 값"}]

    def test_code_fence_lines_skipped(self):
        text = "```json\n" + FULL + "\n```"
        turn = _collect([text])
        assert len(turn.edits) == 2
        assert turn.truncated is False

    def test_think_block_skipped(self):
        text = "<think>\n{'생각': '중'}\n</think>\n" + FULL
        turn = _collect([text])
        assert len(turn.edits) == 2
        assert turn.reply == "수정했습니다"

    def test_on_edit_callback_order(self):
        seen: list[int] = []
        turn = _collect([FULL], on_edit=lambda e: seen.append(e["id"]))
        assert seen == [1, 2]
        assert len(turn.edits) == 2


class TestTruncatedStream:
    def test_tail_cut_mid_string_keeps_prior_lines(self):
        """마지막 edit 줄이 문자열 중간에서 잘림 → 앞 줄들만 채택 + truncated."""
        text = (
            '{"reply": "채움"}\n'
            '{"id": 1, "new_text": "완성"}\n'
            '{"id": 2, "new_text": "여기서 잘'
        )
        turn = _collect([text])
        assert turn.edits == [{"id": 1, "new_text": "완성"}]
        assert turn.truncated is True

    def test_tail_salvage_when_values_complete(self):
        """꼬리 줄의 id·new_text가 완결돼 있으면(닫는 괄호만 없음) 구제."""
        text = (
            '{"reply": "채움"}\n'
            '{"id": 1, "new_text": "완성"}\n'
            '{"id": 2, "new_text": "이것도 완성"'
        )
        turn = _collect([text])
        assert turn.edits == [
            {"id": 1, "new_text": "완성"},
            {"id": 2, "new_text": "이것도 완성"},
        ]
        assert turn.truncated is True  # 이후 줄(notes 등) 유실은 사실

    def test_tail_partial_new_text_never_fabricated(self):
        """잘린 new_text를 빈 값으로 날조하지 않는다 (노드 삭제 사고 방지)."""
        text = '{"reply": "r"}\n{"id": 7, "new_text": "잘린 값'
        turn = _collect([text])
        assert turn.edits == []
        assert turn.truncated is True


class TestContractViolation:
    def test_single_object_one_line_absorbed(self):
        """한 줄짜리 통 JSON(계약 위반이지만 흔함) → 헤더 줄에서 edits 흡수."""
        text = ('{"reply": "완료", "edits": [{"id": 1, "new_text": "값1"}, '
                '{"id": 2, "new_text": "값2"}], "notes": "n"}')
        turn = _collect([text])
        assert turn.parsed_lines == 1
        assert turn.edits == [
            {"id": 1, "new_text": "값1"},
            {"id": 2, "new_text": "값2"},
        ]
        assert turn.notes == "n"

    def test_pretty_printed_object_yields_zero_parsed_lines(self):
        """여러 줄 pretty-print 통 JSON → 줄 파싱 0건 + raw 보존 (폴백 신호)."""
        text = '{\n  "reply": "완료",\n  "edits": []\n}'
        turn = _collect([text])
        assert turn.parsed_lines == 0
        assert turn.edits == []
        assert turn.raw == text
        assert turn.truncated is False  # 폴백이 판단할 몫

    def test_empty_stream(self):
        turn = _collect([])
        assert turn.parsed_lines == 0
        assert turn.raw == ""
        assert turn.truncated is False
