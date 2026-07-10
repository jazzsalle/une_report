# 프로젝트: LLM 대화 기반 hwpx 문서 생성·편집 도구

## 개요

- "재난 계획서 생성도구" 고도화를 위한 사전 검증 프로젝트. 기존 시스템에 붙이지 않고 **독립형**으로 개발한다.
- 최종 목표: LLM과의 대화로 hwpx 문서를 편집·생성하고 결과물(hwpx·docx)을 즉시 출력하는 도구.
- 설계/요구사항의 원본은 루트의 `개발 배경 및 목적.txt`이다.
- 참조 오픈소스: process-gpt, process-gpt-completion, process-gpt-vue3, rhwp, hwpConverter.
- LLM 연동은 UNI RAG System API(`http://221.147.100.161:8000/docs`)를 사용한다.

## 작업 방식

- 오픈소스 원본을 다운로드(클론)할 때는 반드시 `hwpx_sources/` 폴더에 적재하여 활용한다. 이 폴더는 참조용이므로 git에 커밋하지 않는다(.gitignore 등재).

- Phase 실행은 `/phase-run N` 스킬로 한다 (planner → generator 병렬 → evaluator 사이클).
- Phase 합격 기준은 `evaluation_criteria.md`에 있다.
- 세션을 마칠 때는 `/handoff`, 이어서 할 때는 `/resume-work` 또는 세션 시작 시 자동 주입되는 `PROGRESS.md`를 따른다.
- 빌드/실행 명령 (Windows 기준, 저장소 루트에서):
  - 백엔드 의존성: `python -m venv .venv` → `.venv/Scripts/python -m pip install -r requirements.txt`
  - 백엔드 실행: `.venv/Scripts/python -m uvicorn app.main:app --port 8080`
  - 프론트 빌드: `cd web && npm install && npm run build` (산출물 web/dist를 백엔드가 서빙)
  - 프론트 개발 서버: `cd web && npm run dev` (5173 포트, /api는 8080으로 프록시)
  - 테스트: `.venv/Scripts/python -m pytest`
  - 환경설정: `.env.example`을 `.env`로 복사 (테스트 계정 등)

## Phase

| Phase | 상태 | 목표 | 산출물 |
|---|---|---|---|
| 1 | ✅ 완료 | 오픈소스·자료 조사 분석 (Process-GPT 3종+office-mcp, rhwp, hwpConverter, UNI RAG API) | `docs/analysis.md` |
| 2 | ✅ 완료 | 시스템 설계 (모듈별 요구사항 M1~M6, 시나리오 A~D, 시퀀스 다이어그램, 아키텍처·스택 확정, API·DB 설계, 사용자 제공 항목) | `DESIGN.md` |
| 3 | 대기 | 스캐폴딩: FastAPI+Vue3 골격, SQLite 스키마, M1~M6 모듈 패키지 구조, Dockerfile | 빌드·실행 가능한 골격 |
| 4 | 대기 | hwpx 코어(M1): extract/parse/to_html(data-id)/apply_edits/repack + placeholder 검증 + 실양식 실측 | M1 모듈 + 단위 테스트 |
| 5 | 대기 | LLM 연동(M3): UNI RAG 로그인·chat 래퍼·응답 스키마 실측·JSON 복구 파서·품질 실험 | M3 모듈 + 통합 테스트 |
| 6 | 대기 | 오케스트레이션+UI(M4·M5·M6): 의도 분류, 편집 파이프라인, 채팅+미리보기 UI, hwpx(여유 시 docx=M2) 다운로드 | 시나리오 A·B end-to-end 데모 |
| 7 | 대기 | 통합 테스트·문서화: 시나리오 A~D 검증, 한컴오피스 렌더링 확인 절차, README | 테스트 통과 + 사용 가이드 |

핵심 설계 결정 (상세는 `DESIGN.md`): HWPX↔HTML 왕복 편집(process-gpt-office-mcp 방식 응용, `data-id` 앵커 + `edits[{id,new_text}]` 부분 수정), 템플릿 우선, Python 3.11+/FastAPI + Vue3 경량 SPA, SQLite+로컬 파일저장소, LLM은 UNI RAG `/chat/`(qwen3-coder-next, OpenAI 호환 추상화로 교체 가능).
