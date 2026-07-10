# PROGRESS

## Last updated
2026-07-11

## Current goal
LLM 대화 기반으로 hwpx 문서를 생성·편집하고 결과물(hwpx·docx)을 즉시 출력하는 독립형 도구 개발.
(설계 원본: `개발 배경 및 목적.txt`, Phase 정의: `CLAUDE.md`, 합격 기준: `evaluation_criteria.md`)

## Done this session
- 하네스/핸드오프 장치 구성, **Phase 1 완료**(`docs/analysis.md`), **Phase 2 완료**(`DESIGN.md`) — 커밋 2b84163
- 사용자 지시로 Phase 7까지 계속 진행 개시. UNE 테스트 계정은 `개발 배경 및 목적.txt`에 기재됨 (⚠ 원격 push 전 제거 권장)
- 규칙 추가: 오픈소스 원본 클론은 `hwpx_sources/`에 적재 (CLAUDE.md 기록, gitignore 등재). process-gpt-office-mcp 클론 완료 → `hwpx_sources/process-gpt-office-mcp`
- **Phase 3 사실상 완료** (커밋 직전 상태):
  - app/ 골격: config, db(schema.sql·database.py), api(health·auth·documents·chat 라우터), llm(uni_rag_client 로그인만 구현), core/hwpx·core/docx·services 빈 패키지
  - web/ Vite+Vue3 골격: npm install + build 성공 (web/dist 생성)
  - .venv 생성, requirements 설치 성공 (fastapi/uvicorn/httpx/python-docx/python-hwpx/pytest)
  - 스모크 테스트 통과: TestClient로 GET /api/health → 200 {"status":"ok","uni_rag":"up"} (UNI RAG 서버 접속 확인됨)
  - Dockerfile, .env.example 작성. CLAUDE.md에 빌드/실행 명령 기록
  - 미완: evaluator 공식 채점은 생략(체크리스트는 수동 확인 완료), documents/chat 라우터는 501 스텁(Phase 6 예정)

## In progress
- Phase 4 착수 전 (다음 작업)

## Next steps
1. Phase 4: hwpx 코어(M1) 구현 — `app/core/hwpx/`에 package(압축해제/재압축)·parser·html_renderer(data-id)·editor(apply_edits)·placeholder. 참조: `hwpx_sources/process-gpt-office-mcp`의 `office_mcp/formats/hwpx/`(runner.py·hwpx_to_html.py·hwpx_edit.py). 테스트 픽스처는 python-hwpx로 생성, pytest 작성
2. Phase 5: `app/llm/uni_rag_client.py`에 chat() 추가 + **/chat/ 응답 스키마 실호출 확인**(계정: 개발 배경 및 목적.txt, .env의 TEST_UNE_ACCOUNT/PASSWORD에 복사해 사용)
3. Phase 6: 오케스트레이터(M4)·documents/chat 라우터 실구현·Vue UI(채팅+미리보기+다운로드)
4. Phase 7: 통합 테스트·README
- 태스크 목록: #1 Phase3(진행중→완료 처리 필요) #2~#5 Phase4~7 pending

## Blockers
- 없음

## Blockers
- 없음

## How to run
- 아직 코드 없음. Phase 3(스캐폴딩) 완료 후 기록 예정.
- Phase 실행: `/phase-run N`
