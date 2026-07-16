# PROGRESS

## Last updated
2026-07-16 (회사 PC 세션 종료 — 집에서 이어서 작업)

## Current goal
**T3Q 재난안전계획서 생성 도구 전환** (`upgrade/업그레이드 지시사항.txt`,
설계 `docs/t3q_upgrade_design.md`). 기준정보 입력 → 목차 생성(API-RPT-001) →
목차 편집 → 본문 스트리밍(API-RPT-002) → hwpx/docx 내보내기.
기존 hwpx 대화 편집은 [hwpx 편집] 탭으로 공존 (LLM은 API-LLM-001).

## 현재 브랜치
`feature/t3q-report-generator` (base: feature/core-improvements — 미push 상태 확인 필요)

## Done this session (2026-07-14)
- **T3Q 전환 5단계 구현 완료** (설계 §7 커밋 단위 그대로):
  1. `t3q_client.py` — RPT-001 목차·RPT-002 본문 SSE(섹션 오류 항목 전달, [DONE] 부재=중단 예외)·
     LLM-001 OpenAI 호환 백엔드(finish_reason 보존). config `T3Q_*`, certs/cadm-ca.crt. MockTransport 14건
  2. **로그인 삭제**: routes_auth·uni_rag_client·관련 테스트 제거, get_current_user → 익명 로컬 사용자,
     편집 모드 백엔드 T3qChatBackend 교체 (오케스트레이터 무수정), 헬스체크 T3Q 프로브
  3. `routes_report.py`(toc/content SSE: status→section*→done, section_error 격리, error+received)·
     `report_builder.py`(마크다운 문단/표 분해→hwpx·docx 조립, 참조문서 표기, 리프 평면화)
  4. 프론트 전면 개편: 모드 탭, CriteriaPanel(명세 항목명+요구사항 폼 선택지, 빈 값 키 생략),
     TocView(수정·추가/삭제·▲▼ 순서), ReportView(목차별 상태+스트리밍 렌더+내보내기), 로그인 UI 삭제
  5. e2e 플로우 테스트 + `scripts/probe_t3q.py`(toc/content/llm) + README·limitations 갱신
- 테스트 **264 passed** (UNI RAG 테스트 삭제분 반영)
- **실서버 검증 완료**: /api/report/toc 경유 실제 목차 생성 OK, API-LLM-001 chat OK(finish_reason=stop),
  헬스체크 t3q=up. 본문 스트리밍은 2026-07-13 직접 실측으로 확인됨(2섹션 43초)
- ⚠ **인증서 이슈**: cadm-ca.crt는 UNE 자체 CA — T3Q 서버(발급자 *.t3q.ai) 검증 불가.
  `.env T3Q_TLS_VERIFY=false` 임시 우회 중. 담당(swpark@unes.co.kr)에 올바른 CA 확인 필요

## Done (2026-07-15~16 추가)
- **표 열폭 160mm 균등 + 새 개요기호마다 줄바꿈** (b5c68d3): report_builder + numbering
  (starts_with_marker/split_outline_runs — 숫자·한글 마커는 날짜 오탐 방지로 줄 시작만)
- **표준 템플릿 서식 적용** (5c6940a): `templates/` + `report_template.py` —
  규약 기반 표본 인식(p0 제목/p1 부제/1.헤딩/가.헤딩/○·- 개조식/빈 표), 템플릿 패키지
  기반 조립(secPr 보존·lineseg 제거·표 리사이즈 160mm·빈 셀 t 생성), docx 근사,
  GET /report/templates + export template·subtitle, ReportView 서식 셀렉트.
  규약: docs/t3q_upgrade_design.md 부록 A. 실서버 내보내기 스모크 OK

## In progress
- **⚠ 신규 템플릿 3종 규약 불일치** — 사용자가 templates/에 추가한
  `기본 템플릿_01/02`, `태풍 상황보고 템플릿`은 현행 표본 규약(1./가./○/-)과 다른 구조:
  - 제목이 **표 박스** 안에 있음 (표0(0,0)="[문서 주제]")
  - 마커 체계가 □(목차)/ㅇ(본문)/-(세부)/*(출처) + "[목차 레벨]"·"[본문 레벨]" 라벨
  - 표 표본에 **표제목(헤더행)/표내용 서식 구분** 있음
  - 태풍 상황보고는 실제 작성 문서 그대로 (표본 문서 아님)
  → 목록에는 나오지만 **내보내기 선택 시 TemplateError(400)**. `AI 행정문서 템플릿`만 동작.
  다음 작업: 인식기를 이 구조(제목 박스·□/ㅇ/-/* 마커·헤더행 표)까지 확장하거나,
  템플릿을 규약에 맞게 수정하거나 — 사용자와 방향 결정 필요.
  (템플릿2는 templates/에서 제거됨 — 원본은 ouputs/에 보관)

## Next steps
1. 브라우저 육안 확인: 생성 도구 전체 플로우 (기준정보→목차→본문 스트리밍→내보내기)
2. 실서버 본문 스트리밍을 UI로 통과시키는 확인 (`probe_t3q.py content`는 확인됨)
3. 올바른 T3Q CA 확보 → certs 교체 → `T3Q_TLS_VERIFY=true` 복귀
4. 내보낸 hwpx 한컴오피스 육안 확인 (`docs/hwpx_validation.md` §3)
5. push + main PR은 사용자 지시 대기
6. (후순위) 요구사항서 잔여: 기준정보 템플릿 관리, 문서보관함, 초안 생성 취소 UI, 이미지 다운로드(RPT-004)

## Blockers
- 인증서 불일치 (§Next 3) — 기능은 우회로 동작 중

## How to run
- 백엔드: `.venv/Scripts/python -m uvicorn app.main:app --port 8080` / 테스트: `.venv/Scripts/python -m pytest`
- 실서버 프로브: `.venv/Scripts/python scripts/probe_t3q.py toc|content|llm`
- 프론트 빌드: `cd web && npm install && npm run build`

## 이전 이력 (feature/core-improvements, 2026-07-12~13)
A·B·C 코어 개선(빈 셀 편집·가이드 감지·전량 재작성 fill), 문장 겹침(lineseg)·
미리보기 텍스트 유실(fwSpace tail) 수정, 공문서 항목 기호 자동 부여,
응답 잘림 내성 3층위(부분 복구·청크 격리·JSONL 스트리밍). 상세는 git log 참조.
