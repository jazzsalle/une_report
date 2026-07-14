# 재난안전계획서 생성 도구 (CADM) — T3Q API 기반

기준정보를 입력하고 채팅으로 요청하면 **T3Q 플랫폼의 재난안전계획서 API**가 목차와 본문을 자동 생성하고, 결과를 **hwpx·docx로 즉시 내보내는** 도구입니다. 기존 hwpx 대화 편집 기능(업로드→LLM 편집→다운로드)도 별도 모드로 함께 제공합니다. 로그인 없이 동작하는 **단일 사용자 로컬 도구**입니다.

생성 흐름 (지시·설계: [`docs/t3q_upgrade_design.md`](docs/t3q_upgrade_design.md)):

```
기준정보 입력 → (채팅 "작성해줘") → 목차 자동생성(API-RPT-001)
→ 목차 뷰에서 수정·순서 변경 → 본문 자동생성(API-RPT-002, SSE 스트리밍)
→ 목차별 실시간 문서 작성 → hwpx / docx 내보내기
```

## 아키텍처

`Vue3 SPA (생성 도구 | hwpx 편집)` ⇄ REST/SSE ⇄ `FastAPI` ⇄ `T3Q 플랫폼(plf.mois-disaster.t3q.ai)`, 편집 모드 저장은 `SQLite + data/files/`.

| 모듈 | 역할 | 경로 |
|---|---|---|
| t3q-client | API-RPT-001(목차)·API-RPT-002(본문 SSE)·API-LLM-001(OpenAI 호환 LLM) 래퍼 | `app/llm/t3q_client.py` |
| report | 생성 API(toc/content SSE/export), 마크다운→hwpx·docx 조립 | `app/api/routes_report.py`, `app/services/report_builder.py` |
| hwpx-core | hwpx 압축 해제/재압축, XML 파싱, HTML 변환(`data-id`), 편집 적용, 신규 hwpx 생성 | `app/core/hwpx/` |
| docx-core | docx 출력 (python-docx) | `app/core/docx/` |
| chat-orchestrator | 편집 모드: 의도 분류(edit/fill/query), 편집·채움 파이프라인, JSONL 스트리밍 잘림 내성 | `app/services/` |
| web-ui | 기준정보 입력 패널·목차 편집 뷰·본문 스트리밍 뷰 + 기존 편집 화면 | `web/` |

## 요구사항

- Python 3.11+
- Node.js (프론트 빌드용, npm 포함)
- T3Q 플랫폼(`https://plf.mois-disaster.t3q.ai`) 접근 가능한 네트워크 — 목차·본문 생성과 편집 모드 LLM 대화에 필요. 접근 불가 시에도 hwpx 업로드·미리보기·다운로드는 동작

## 설치

저장소 루트에서 (Windows 기준):

```bash
# 1. 백엔드 의존성
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 2. 환경설정 — .env.example을 .env로 복사 (.env는 gitignore됨)
copy .env.example .env
#    ⚠ TLS: 현재 배포된 CA(certs/cadm-ca.crt)로는 T3Q 서버 검증이 안 됨
#      (발급자 불일치 — docs/t3q_upgrade_design.md §6). 올바른 CA 확보 전까지
#      .env에 T3Q_TLS_VERIFY=false 로 우회 필요.

# 3. 프론트 빌드 (산출물 web/dist를 백엔드가 서빙)
cd web && npm install && npm run build
```

## 실행

```bash
# 백엔드 (web/dist가 있으면 http://127.0.0.1:8080 에서 UI까지 서빙)
.venv/Scripts/python -m uvicorn app.main:app --port 8080

# (선택) 프론트 개발 서버 — 5173 포트, /api는 8080으로 프록시
cd web && npm run dev

# (선택) T3Q 실서버 스모크 프로브
.venv/Scripts/python scripts/probe_t3q.py toc      # 목차 생성
.venv/Scripts/python scripts/probe_t3q.py content  # 본문 스트리밍
.venv/Scripts/python scripts/probe_t3q.py llm      # 편집 모드 LLM
```

## 테스트

```bash
# 단위·e2e 테스트 (T3Q 실서버 불필요 — 전부 Mock/Fake)
.venv/Scripts/python -m pytest
```

## 사용법 개요 — 생성 도구 모드

1. 브라우저 접속 (로그인 없음) → 상단 탭 **[생성 도구]**
2. 왼쪽 **기준정보 입력 패널**에 문서 주제·배경정보(재난유형/관리단계 등)·내용지침·표현규칙·문서 작성 목적을 입력 (필수 항목 *)
3. 채팅창에 **"재난안전계획서를 작성해줘"** → 목차가 자동 생성됨
4. 오른쪽 **목차 뷰**에서 항목 수정·추가/삭제·▲▼ 순서 변경 → **[본문 생성]**
5. 목차별로 본문이 **실시간 스트리밍**되어 문서에 작성됨 (대기/생성 중/완료/오류 상태 표시, 참조문서 표기)
6. **[hwpx 내보내기] / [docx 내보내기]** 로 저장

## 사용법 개요 — hwpx 편집 모드 (기존 기능)

1. 상단 탭 **[hwpx 편집]** → **hwpx 파일 업로드**
2. 채팅으로 편집 지시 (예: "2페이지 과제명을 ○○로 수정해줘") — LLM은 T3Q API-LLM-001 사용
3. 미리보기에서 문단/셀 클릭 선택 후 지시하면 해당 요소만 편집
4. **hwpx 다운로드** (원본 서식 보존 재패키징)

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/t3q_upgrade_design.md`](docs/t3q_upgrade_design.md) | T3Q 전환 설계 — API 매핑, 아키텍처, 인증서 이슈 |
| [`DESIGN.md`](DESIGN.md) | (구) 시스템 설계서 — hwpx 편집 모드의 기반 설계 |
| [`docs/hwpx_validation.md`](docs/hwpx_validation.md) | hwpx 출력물 검증 절차 |
| [`docs/limitations.md`](docs/limitations.md) | 알려진 제약사항·향후 과제·보안 주의사항 |
| `upgrade/` | 업그레이드 지시사항, MOIS API 명세서, 요구사항정의서 |

## 보안 주의

> - `.env`는 gitignore에 등재되어 있습니다 — 커밋하지 마세요.
> - `T3Q_TLS_VERIFY=false` 우회는 임시입니다. 올바른 T3Q CA 확보 후 `certs/`에 교체하고 검증을 켜세요 ([`docs/t3q_upgrade_design.md`](docs/t3q_upgrade_design.md) §6).
> - T3Q API는 현재 무인증입니다(실측). 인증이 도입되면 `app/llm/t3q_client.py`에 헤더 주입 지점이 일원화되어 있습니다.
