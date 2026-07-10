# hwpx-chat-editor — LLM 대화 기반 hwpx 문서 생성·편집 도구

웹 화면에서 LLM과 **대화 한번으로** hwpx 문서를 생성·수정하고, 결과물(hwpx·docx)을 **즉시 미리보기·다운로드**하는 도구입니다. "재난 계획서 생성도구" 고도화를 위한 사전 검증(PoC) 프로젝트로, 기존 시스템에 붙이지 않는 **독립형**으로 개발되었습니다.

핵심 방식은 **HWPX ↔ HTML 왕복 편집**입니다. 서버가 hwpx를 `data-id` 앵커가 주입된 HTML로 변환해 브라우저는 HTML만 렌더하고, LLM은 `edits: [{id, new_text}]` 형태의 부분 수정만 반환하며, 저장 시 변경분을 원본 XML 노드에 적용해 재압축합니다. 원본 서식 보존과 프론트 단순화를 동시에 얻는 구조이며, 인프라는 SQLite + 로컬 디스크만 사용하는 단일 서버 프로세스입니다.

## 아키텍처

`Vue3 SPA (채팅+미리보기)` ⇄ REST/SSE ⇄ `FastAPI (M5→M4→M3/M1/M2)` ⇄ `UNI RAG System (/auth/login, /chat/)`, 저장은 `SQLite + data/files/`.

| 모듈 | 역할 | 경로 |
|---|---|---|
| M1 hwpx-core | hwpx 압축 해제/재압축, XML 파싱, HTML 변환(`data-id` 주입), 편집 적용, placeholder 검증, 표 청킹, 신규 hwpx 생성 | `app/core/hwpx/` |
| M2 docx-core | 편집 결과의 docx 동시 출력 (내용 전달용, python-docx) | `app/core/docx/` |
| M3 llm-client | UNI RAG 로그인(JWT)·chat 래퍼(SSE 포함), LLM JSON 복구 파서, 백엔드 추상화(`LLMBackend`) | `app/llm/` |
| M4 chat-orchestrator | 의도 분류(edit/fill/query), 편집·채움 파이프라인, 대화 히스토리 | `app/services/` |
| M5 api | REST + SSE 엔드포인트, 파일/버전 저장소, 인증(UNE 위임) | `app/api/`, `app/db/` |
| M6 web-ui | 좌측 채팅 + 우측 미리보기(DOMPurify), 변경 하이라이트, 선택 편집, 다운로드 | `web/` |

상세 설계(요구사항 M1~M6, 시나리오 A~D, 시퀀스 다이어그램, API·DB 스키마)는 [`DESIGN.md`](DESIGN.md) 참조.

## 요구사항

- Python 3.11+
- Node.js (프론트 빌드용, npm 포함)
- (선택) UNI RAG System(`http://221.147.100.161:8000`)에 접근 가능한 네트워크 — LLM 대화 기능에 필요. 접근 불가 시에도 문서 업로드·미리보기·수동 편집·다운로드는 동작

## 설치

저장소 루트에서 (Windows 기준):

```bash
# 1. 백엔드 의존성
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 2. 환경설정 — .env.example을 .env로 복사한 뒤 값 채우기 (.env는 gitignore됨)
copy .env.example .env

# 3. 프론트 빌드 (산출물 web/dist를 백엔드가 서빙)
cd web && npm install && npm run build
```

## 실행

```bash
# 백엔드 (web/dist가 있으면 http://127.0.0.1:8080 에서 UI까지 서빙)
.venv/Scripts/python -m uvicorn app.main:app --port 8080

# (선택) 프론트 개발 서버 — 5173 포트, /api는 8080으로 프록시
cd web && npm run dev
```

## 테스트

```bash
# 단위·e2e 테스트 (UNI RAG 실서버 불필요 — integration 마커는 기본 제외)
.venv/Scripts/python -m pytest

# UNI RAG 실서버 통합 테스트 (.env에 TEST_UNE_ACCOUNT/TEST_UNE_PASSWORD 필요)
.venv/Scripts/python -m pytest -m integration
```

## 사용법 개요

1. 브라우저에서 접속 후 **UNE 계정으로 로그인** (인증은 UNI RAG에 위임, 비밀번호는 저장하지 않음)
2. **hwpx 파일 업로드** — 우측 패널에 미리보기가 렌더됨
3. **대화로 편집** — 채팅에 지시 입력 (예: "2페이지 과제명을 'hwpx 문서 생성 고도화'로 수정해줘"). 양식 채움("이 양식으로 ○○ 계획서 초안 작성해줘")과 일반 질의도 의도 분류로 자동 처리
4. **부분 선택 편집** — 미리보기에서 문단/셀을 클릭 선택한 뒤 지시하면 해당 요소만 대상
5. 편집 결과는 미리보기에 즉시 반영되고 **변경 부위가 하이라이트**됨 (편집 1회 = 버전 1개)
6. **hwpx / docx 다운로드** — hwpx는 원본 서식 보존 재패키징, docx는 내용 전달용 단순 변환

구체적인 재현 절차는 [`docs/demo_scenarios.md`](docs/demo_scenarios.md) 참조.

## 문서

| 문서 | 내용 |
|---|---|
| [`DESIGN.md`](DESIGN.md) | 시스템 설계서 — 요구사항, 아키텍처, API·DB 설계 |
| [`docs/analysis.md`](docs/analysis.md) | Phase 1 오픈소스·자료 조사 분석 (Process-GPT, rhwp, hwpConverter, UNI RAG API) |
| [`docs/demo_scenarios.md`](docs/demo_scenarios.md) | 데모 시나리오 재현 절차 |
| [`docs/hwpx_validation.md`](docs/hwpx_validation.md) | hwpx 출력물 검증 절차 (자동 검증 + 한컴오피스 육안 확인 + 실양식 실측) |
| [`docs/uni_rag_chat_schema.md`](docs/uni_rag_chat_schema.md) | UNI RAG `/chat/` 응답 스키마 실측 결과 |
| [`docs/limitations.md`](docs/limitations.md) | 알려진 제약사항·향후 과제·보안 주의사항 |

## 보안 주의

> **⚠ 커밋·공유 전 반드시 확인**
>
> - `.env`와 UNE 계정/비밀번호 값은 **절대 커밋하지 마세요** (`.env`는 gitignore에 등재되어 있음).
> - 루트의 `개발 배경 및 목적.txt`에 **테스트 실계정이 기재되어 있습니다**. 저장소를 원격에 push하기 전에 해당 계정 정보를 반드시 제거하세요.
> - UNI RAG 구간은 평문 HTTP입니다. 사내망 전제로만 사용하세요. (상세: [`docs/limitations.md`](docs/limitations.md))
