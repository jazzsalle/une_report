# PROGRESS

## Last updated
2026-07-11

## Current goal
LLM 대화 기반으로 hwpx 문서를 생성·편집하고 결과물(hwpx·docx)을 즉시 출력하는 독립형 도구 개발.
(설계 원본: `개발 배경 및 목적.txt`, Phase 정의: `CLAUDE.md`, 합격 기준: `evaluation_criteria.md`)

## Done this session
- Phase 1~3 완료 (커밋 2b84163, 8a9a714). 상세는 git log 참조.
- **Phase 4 완료 — evaluator PASS** (hwpx 코어 M1, `app/core/hwpx/`):
  - `package.py`: extract/repack(엔트리 순서·압축방식·mimetype STORED 보존, 서명 skip) + `validate_hwpx`(zip·mimetype·header/section/content.hpf 존재·XML 파스 검사)
  - `parser.py`: `parse_section` — 문단·표셀 TextNode 파싱, 중첩 표는 내부 셀만, 순번 규칙 docstring 명기
  - `html.py`: `hwpx_to_html` — self-contained HTML 문자열 반환, `.page` 분할, charPr/paraPr/borderFill→CSS, 표 병합, BinData base64 인라인. **data-id는 parse_section 노드를 소비해 매핑(불변식: data-id 집합 == 전역 id 집합, 어긋나면 RuntimeError)**
  - `edits.py`: `apply_edits(hwpx, edits[{id,new_text}], out)` — 전역 id=섹션 로컬+offset, 첫 hp:t 교체·나머지 비움(서식 보존), 동일 텍스트/미존재 id는 skip, 섹션 XML 스냅샷 반환. `normalize_edit_id`는 12/"12"/"p-0012" 허용
  - `placeholder.py`(4유형 탐지·verify_output), `chunker.py`(표 셀 불분할 청크), `builder.py`(python-hwpx로 새 hwpx 생성, placeholders_demo 지원)
  - `tests/` 4개 파일 **53 passed** (`.venv/Scripts/python -m pytest`), `docs/hwpx_validation.md`(자동 검증 항목 + 한컴 육안 확인 절차 + 실양식 실측 절차)
  - 참조 구현(office-mcp)은 LICENSE 미확인이라 코드 미복사, 구조만 응용해 자체 작성
  - ⚠ 실양식(재난 계획서 hwpx) 미확보 — python-hwpx 동봉 템플릿으로 대체 실측, 입수 시 재실측 체크리스트가 hwpx_validation.md 4장에 있음. 한컴오피스 육안 확인은 Phase 7로 이월
- **Phase 5 완료 — evaluator PASS** (LLM 연동 M3, `app/llm/`):
  - `/chat/` 응답 스키마 실측 완료(`docs/uni_rag_chat_schema.md`, `scripts/probe_uni_rag.py`): stream=false는 `{answer, sources[]}`, SSE는 `data: "델타"`(JSON 문자열)·`{"__sources__":...}`·`[DONE]` 종료
  - `base.py`: `LLMBackend` ABC + 예외 계층(LlmError/LlmAuthError/LlmUnavailableError/LlmTimeoutError/LlmJsonParseError) — OpenAI 호환 교체 대비
  - `json_parser.py`: `parse_llm_json` 4단계 복구(원문→펜스/<think> 제거→괄호 균형 추출→따옴표·트레일링 콤마 보정)
  - `uni_rag_client.py`: `chat()`(stream=false, answer 추출)·`chat_stream()`(SSE) + 장애 매핑(401/403→Auth, 5xx·연결불가→Unavailable, 타임아웃→Timeout) + MockTransport 주입 지점
  - `pytest.ini`(integration 마커 기본 제외). 기본 pytest **95 passed**, `-m integration` 실서버 **2 passed**(실로그인+chat+JSON 지시 준수 확인)
  - 품질 1차 실측: qwen3-coder-next가 공문서 어투 교정·순수 JSON 응답 모두 양호
  - `.env` 생성(테스트 계정 기입, gitignore 등재 확인 — 커밋 금지 대상)
- **Phase 6 완료 — evaluator PASS** (오케스트레이션 M4 + API M5 + UI M6):
  - `app/services/orchestrator.py`+`prompts.py`: 의도 분류(edit/fill/query, LLM 분류+휴리스틱 폴백), edit 파이프라인(JSON 재요청 1회·무효 id 필터), fill 파이프라인(placeholder 청크 분할·병합), `TurnResult` 계약
  - `app/services/document_store.py`: 업로드→v0, 버전 관리(v{N}.hwpx+HTML 캐시), get_nodes/get_placeholders(전역 id), apply_document_edits, export(hwpx+**docx**=M2-1 구현됨, `app/core/docx/exporter.py`)
  - `app/api/`: routes_documents(업로드/preview/export/versions), routes_chat(**SSE**: status→token→document_updated→done, error 매핑), routes_sessions(메시지 이력)
  - `web/src/`: LoginForm·ChatPanel·PreviewPanel + api.js(fetch 기반 POST SSE 파서), DOMPurify 렌더, data-id 하이라이트·클릭 선택(selection), hwpx 다운로드. `npm run build` 성공(web/dist)
  - 테스트: **143 passed, 2 deselected** — e2e 시나리오 A(양식 채움 fill→edit→export→placeholder 전원 해소)·B(문단 수정·원문 보존) FakeBackend로 전 구간 검증
  - `docs/demo_scenarios.md`: 시나리오 A·B 재현 절차(명령어 수준)·한컴 육안 확인 항목·실 LLM 주의사항
- **Phase 7 완료 — evaluator PASS** (통합 테스트·문서화): 시나리오 C(선택 편집)·D(질의 후 문서 반영) e2e 테스트 추가 → 전체 **146 passed, 2 deselected**. `README.md`(설치·실행·사용법·문서 링크·보안 주의), `docs/limitations.md`(제약 7항목·향후 과제 7건), `docs/demo_scenarios.md`를 시나리오 A~D 전체 가이드로 확장(한컴오피스/Word 육안 확인 절차 포함)
- **전 Phase(1~7) 완료** — 프로젝트 목표 달성 (독립형 hwpx 대화 편집 도구 PoC)
- UNE 테스트 계정은 `개발 배경 및 목적.txt`에 기재됨 (⚠ 원격 push 전 제거 권장)

## In progress
- 없음 — Phase 4~7 산출물 Phase별 4개 커밋으로 커밋 완료 (사용자 승인, scripts/probe_output.txt 포함)

## Next steps
1. (선택) 실양식 재난 계획서 hwpx 입수 시 `docs/hwpx_validation.md` §4.3 재실측 체크리스트 수행
2. (선택) 한컴오피스에서 데모 산출물 육안 확인 (`docs/demo_scenarios.md` §6)
3. (선택) `개발 배경 및 목적.txt`의 실계정 제거 후 원격 push

## Blockers
- 없음

## How to run
- 백엔드: `.venv/Scripts/python -m uvicorn app.main:app --port 8080` / 테스트: `.venv/Scripts/python -m pytest` (상세는 CLAUDE.md)
- Phase 실행: `/phase-run N`
