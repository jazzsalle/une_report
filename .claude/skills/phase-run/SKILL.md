---
name: phase-run
description: "/phase-run N 또는 'Phase N 실행' 요청 시 호출. Phase를 planner→generator(병렬)→evaluator 사이클로 오케스트레이션한다."
allowed-tools: Read, Edit, Write, Bash(git *), Task
---

# Phase 실행 오케스트레이션

사용자가 `/phase-run N`(또는 "Phase N 실행")을 요청하면, **메인 세션이 직접** 아래 사이클을 오케스트레이션한다. N은 `CLAUDE.md`의 "## Phase" 섹션에 정의된 Phase 번호다.

## 절차

### a) 계획

`planner` subagent를 Task 도구로 띄워 Phase N을 태스크 목록으로 분해시킨다.
프롬프트에 Phase 번호·목표·산출물(CLAUDE.md 발췌)과 evaluation_criteria.md의 해당 Phase 기준을 포함한다.

### b) 병렬 디스패치

planner가 반환한 태스크 목록을 보고:

- `[PARALLEL]` 태스크들 → 각각 `generator` subagent를 **Task 도구로 동시에**(한 메시지에 여러 Task 호출) 띄워 병렬 처리한다.
- `[AFTER: X]` 태스크 → 선행 태스크 X가 완료된 후 순차 실행한다.

병렬화는 generator 내부가 아니라 **이 스킬에서 Task를 여러 개 띄우는 방식**으로 한다.
각 generator에는 해당 태스크의 명세(목표·대상 파일·완료 기준) 전체를 전달한다.

### c) 평가

모든 태스크 완료 후 `evaluator` subagent를 띄워 `evaluation_criteria.md` 기준으로 Phase N을 검증시킨다.

### d) FAIL 처리

- evaluator의 **거절 노트를 그대로 generator에 전달**해 수정시킨 뒤 다시 c)로 돌아간다.
- 이 수정 루프는 **최대 3회**. 3회 후에도 FAIL이면 멈추고, 남은 미달 항목과 시도 내역을 사용자에게 보고한다.

### e) PASS 처리 — ★ PROGRESS.md 갱신 필수

PASS가 나오면 **반드시 `PROGRESS.md`를 갱신**한다 (핸드오프 장치와의 연결선이므로 생략 금지):

- `Last updated`: 오늘 날짜
- `Current goal`: 프로젝트 전체 목표 (변동 없으면 유지)
- `Done this session`: 이번 Phase에서 만든 산출물 요약
- `Next steps`: 다음 Phase 번호와 목표

그런 다음:

1. "Phase N 완료"를 선언하고 결과를 요약한다.
2. `git add` 대상과 **커밋 메시지를 제안**한다. (커밋은 사용자 확인 후, **자동 push 금지**)
