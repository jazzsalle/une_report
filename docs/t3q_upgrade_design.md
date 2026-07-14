# T3Q 재난안전계획서 생성 도구 전환 설계

- 작성: 2026-07-14, 브랜치 `feature/t3q-report-generator`
- 근거: `upgrade/업그레이드 지시사항.txt`, `upgrade/(251124)MOIS_API_명세서_v0.8.5.xlsx`(API-RPT-001/002·API-LLM-001 상세 추출),
  `upgrade/CADM_PA_2121_01_요구사항정의서_업무기능분해도_V0.2.6.xlsx`(UFR-* 폼 항목·선택지),
  T3Q 실서버 실측(2026-07-13 RPT-002 응답 수신 성공, 2026-07-14 인증서 검증)
- 화면설계서 PDF는 렌더러 부재로 미열람 — 요구사항정의서의 화면유무·기능분해(FUN-CADM-*)로 대체 차용

## 1. 배경·목표

UNI RAG(`/chat/`) + 사내 계정 로그인 기반이던 도구를, T3Q 플랫폼의 재난안전계획서 API로
전환한다. 핵심 흐름: **기준정보 입력 → (채팅 트리거) 목차 자동생성(API-RPT-001) →
목차 뷰에서 조회·편집·순서변경 → 본문 자동생성(API-RPT-002, SSE 스트리밍) →
목차별 실시간 문서 작성 → hwpx/docx 내보내기**.

### 사용자 확정 사항 (2026-07-14)

| 결정 | 내용 |
|---|---|
| 기존 hwpx 편집 기능 | **두 모드 공존** — [생성 도구]·[hwpx 편집] 탭 분리. 편집 모드의 LLM 백엔드는 UNI RAG → **API-LLM-001**(OpenAI 호환, model="mois")로 교체 |
| 출력 형식 | **hwpx + docx** (기존 builder·exporter 재활용, PDF는 브라우저 인쇄로 갈음) |
| 로그인 | **완전 삭제** (지시 0번). T3Q SSO(UFR-AUTH-01)도 이번 범위 제외 — 익명 단일 사용자 |
| 저장 범위 | **생성 플로우만(최소)** — 문서보관함·기준정보 템플릿 관리·최근문서는 범위 외 |

## 2. 대상 API (실측 확인)

베이스 URL: `https://plf.mois-disaster.t3q.ai` (config `T3Q_BASE_URL`)

### API-RPT-001 — 목차 자동생성 `POST /model-api/ae894/reports/plan/toc`
- 요청 `{"data": {...기준정보}}` → 응답 `{"title": str, "sections": [{"name", "children": [...]} 재귀]}`

### API-RPT-002 — 본문 자동생성 `POST /model-api/ae894/reports/plan/content`
- 요청 = 기준정보 + `sections`(RPT-001 응답 형태) + `stream`(기본 true)
- 스트리밍 응답: 리프 섹션당 `data: {"name", "content"(마크다운, 표 포함), "references": [{id, fileId, fileName, page}]}` 한 줄,
  오류 시 `data: {"name", "requestId", "error"}`, 종료 `data: [DONE]`
- 2026-07-13 실측: 최소 스키마로 200 OK, 2개 섹션 약 43초, 마크다운 표·실문서 인용 확인

### 기준정보(`data`) 공통 스키마 — 입력 패널의 원본

| API 필드 | 필수 | 요구사항 폼 (UFR-*) |
|---|---|---|
| `subject` | Y | 문서 주제 텍스트 |
| `backgroundInfo.disasterType` | Y | 셀렉트 10종: 폭염, 태풍/호우, 지진, 황사, 산불, 감염병, 가축질병, 다중밀집건축물붕괴대형사고, 정부주요시설, 학교시설 |
| `backgroundInfo.controlPhase` | Y | 셀렉트: 예방, 대비 |
| `backgroundInfo.location` | N | 텍스트 |
| `backgroundInfo.startTime/endTime/reportTime` | N | 캘린더+시각, ISO8601(date-time), "선택안함" 허용 |
| `contentInstruction.source` | N | 셀렉트: 문장 끝 괄호 표기 / 주석(줄바꿈) / 선택안함 |
| `contentInstruction.essentialFactors` | N | 텍스트 배열 (예: 법적근거, 최근7일통계) |
| `contentInstruction.writingGuide` | N | 텍스트 200자 이내 |
| `expressionRule.tone` | N | 셀렉트: 개조식 / 서술식 / 선택안함 |
| `expressionRule.maxSentenceLength` | N | 셀렉트: 1문장 80자 이내 / 1문장 110자 이내 / 선택안함 |
| `expressionRule.paragraphSymbol` | N | 셀렉트: "□, ○, ―" / "1.-1.1-1.1.1" / "1.-□-○-―" / 선택안함 |
| `expressionRule.bodytextStart` | N | 셀렉트: 키워드 괄호 요약(제목 제외) / 선택안함 |
| `purposeOfDocument.goalOfBusiness` | Y | 셀렉트: 재난안전계획서 작성 / 선택없음 |
| `purposeOfDocument.role` | Y | 셀렉트: 재난안전계획 수립 담당자 / 행정기관 보고서 작성자 |
| `purposeOfDocument.targetAudiences` | Y | 복수선택 enum: 중앙정부, 지자체, 내부보고, 대민 (서버 검증 실측 확인) |
| `systemPrompt` | N | (패널 미노출 — config/고급 옵션) |

"선택안함"·빈 값 필드는 요청에서 **키 자체를 생략**한다 (RPT-002 실측: 선택 객체 생략 허용).

### API-LLM-001 — LLM 텍스트 생성 `POST /llms/v1/chat/completions` (편집 모드용)
- OpenAI Chat Completions 호환. `model: "mois"` 고정, messages/max_tokens/temperature/stream.
- 응답 `choices[0].message.content` + **`finish_reason`("stop"/"length")** — 기존에 없던 잘림 감지 신호 확보.
- 스트리밍: `data: {"choices":[{"delta":{"content":...}}]}` + `[DONE]` (OpenAI SSE 표준).

## 3. 아키텍처 변경

### 제거
- `app/llm/uni_rag_client.py`(UNI RAG), 로그인 전체: `routes_auth`(로그인/로그아웃), `auth_tokens`·`app_tokens` 의존,
  `web/src/components/LoginForm.vue`, api.js의 Bearer 토큰 처리, `get_current_user` 의존성(→ 익명 단일 사용자로 대체)
- config의 `UNI_RAG_*`, `TEST_UNE_*`

### 신규
- **`app/llm/t3q_client.py`** — httpx 기반, `verify=T3Q_CA_PATH or bool`:
  - `generate_toc(criteria: dict) -> {"title", "sections"}` (RPT-001, 비스트림)
  - `generate_content(criteria, sections) -> AsyncIterator[SectionResult]` (RPT-002 SSE 파싱:
    `data:` JSON 라인 → `{name, content, references}` / 오류 라인 → 예외 아닌 섹션 오류 이벤트 / `[DONE]` 종료)
  - `T3qChatBackend(LLMBackend)` — API-LLM-001로 기존 `chat()/chat_stream()` 계약 구현
    (finish_reason=="length" → truncated 신호 노출, 기존 잘림 내성 사다리와 자연 결합)
- **`app/services/report_builder.py`** — 목차 트리 + 섹션별 마크다운 content → 문서 조립:
  - 스트림은 리프만 평면으로 오므로 **name 문자열로 목차 트리에 매핑**(불일치 시 순서 기반 폴백 + notes)
  - 마크다운 최소 변환: 문단 분리, `| |` 표 → hwpx 표(builder `add_table`), 그 외 서식 기호는 텍스트 보존
  - 기존 `app/core/hwpx/builder.py`(build_hwpx)·`app/core/docx/exporter.py` 재활용, 제목·헤딩 레벨 반영 확장
- **`app/api/routes_report.py`**:
  - `POST /api/report/toc` — 기준정보 검증 → RPT-001 프록시 → `{title, sections}` (재요청 = 재호출)
  - `POST /api/report/content` — SSE: `status`(진행) → `section`(목차별 `{name, content, references, seq}` 도착 즉시) → `done`(조립 결과 요약) / `section_error` / `error`. 생성 중 취소는 클라이언트 연결 종료로 처리(1차)
  - `POST /api/report/export` — `{title, sections(content 포함 트리), format: hwpx|docx}` → 파일 응답
- config: `T3Q_BASE_URL`, `T3Q_CA_PATH`(기본 `certs/cadm-ca.crt`), `T3Q_TLS_VERIFY`(기본 true, 인증서 문제 해결 전 임시 false 가능), `T3Q_TIMEOUT`(본문 생성은 섹션 수에 비례 — 실측 2섹션 43초 → 기본 600s)

### 유지 (편집 모드)
- 문서 업로드→미리보기→대화 편집→내보내기 전체와 그 테스트. 변경점 2가지:
  - LLM 백엔드 주입을 `UniRagClient` → `T3qChatBackend`로 교체 (LLMBackend 추상화 덕에 orchestrator 무수정)
  - `routes_chat`에서 rag_jwt 조회 제거 (토큰 없이 호출), `token` 파라미터는 계약 유지 위해 빈 값 전달

## 4. 프론트 (web/src) — 전면 개편

```
[상단 바]  생성 도구 | hwpx 편집          ← 모드 탭 (로그인 UI 삭제)
─ 생성 도구 ──────────────────────────────
│ 좌: 기준정보 입력 패널   │ 중: 채팅        │ 우: 문서 뷰       │
│ (§2 표의 폼 항목 그대로) │ "~작성해줘" →   │ 목차 뷰 ⇄ 본문 뷰 │
│ 필수값 검증·미리보기     │ 목차 생성 트리거 │                  │
```

- **기준정보 입력 패널** (`CriteriaPanel.vue`): §2 표의 항목명(국문 라벨 + API 필드)·선택지 그대로 구현.
  필수(subject, 재난유형, 관리단계, 업무목적, 역할, 타깃독자) 미입력 시 목차 생성 버튼 비활성 + 필드 하이라이트.
  "기준정보 미리보기" 접이식 영역(UFR-Prompt-01)에 조합된 요청 JSON을 사람이 읽는 형태로 표시.
- **채팅 트리거** (지시 5): 채팅 입력에서 생성/작성 요청(키워드: 작성, 생성, 만들어 등 휴리스틱)을 감지하면
  기준정보 패널 값으로 `/api/report/toc` 호출. 채팅 메시지는 `subject`가 비어 있을 때 주제로 흡수.
  (LLM 의도 분류 불요 — 생성 도구 모드의 채팅은 목차/본문 트리거와 안내 전용)
- **목차 뷰** (`TocView.vue`, 지시 6): RPT-001 트리를 그대로 렌더. 항목별 인라인 텍스트 수정,
  추가(형제/자식)·삭제, **▲▼ 버튼으로 순서 변경**(동일 부모 내, 1차 — 드래그는 후순위), 목차 재요청 버튼.
  편집 완료 → "본문 생성" 버튼 (지시 7).
- **본문 뷰** (`ReportView.vue`, 지시 8): 목차별 카드 — 상태(대기/생성 중/완료/오류) 표시,
  SSE `section` 이벤트 도착 즉시 해당 목차에 content 렌더(마크다운 → HTML, DOMPurify 유지), references 각주 표시.
  전체 완료 후 [hwpx 내보내기] [docx 내보내기] 버튼.
- **hwpx 편집 탭**: 기존 App 화면 그대로 (로그인 게이트만 제거).

## 5. 데이터·저장

- 생성 플로우는 **서버 무저장**(요청-응답 단위). 기준정보·목차·본문은 프론트 상태로만 유지.
- 편집 모드의 SQLite 문서 저장은 유지하되 user 개념 제거: `users`/`app_tokens`/`auth_tokens` 사용 중지,
  문서 소유자 검증은 단일 로컬 사용자 전제로 우회(user_id 상수). 스키마 마이그레이션은 하지 않는다(테이블 잔존 무해).

## 6. 인증서 (지시 9) — ⚠ 실측 결과 불일치

- `upgrade/cadm-ca.zip` → `cadm-ca.crt`(PEM CA)를 `certs/cadm-ca.crt`로 설치하고 httpx `verify`에 지정하는 구조로 구현.
- **그러나 실측(2026-07-14): 이 인증서는 UNE 자체 서명 CA**(CN=ec2-43-200-234-120…amazonaws.com, O=UNE)이고,
  T3Q 서버 리프 인증서의 발급자는 `*.t3q.ai`라 **체인이 연결되지 않아 검증 실패**(CERTIFICATE_VERIFY_FAILED).
  → 올바른 T3Q CA(또는 전체 체인) 확보 전까지 `T3Q_TLS_VERIFY=false`로 우회 운용하고, limitations에 명기.
  cadm-ca.crt는 다른 용도(자체 서비스 HTTPS 등)일 가능성 — 발급 담당(swpark@unes.co.kr) 확인 필요.

## 7. 구현 단계 (커밋 단위)

1. **T3Q 클라이언트**: `t3q_client.py`(RPT-001/002 SSE 파서 + T3qChatBackend) + config + certs 설치 + MockTransport 단위 테스트
2. **로그인 제거**: routes_auth·LoginForm·토큰 의존 제거, 편집 모드 백엔드 교체, 기존 테스트 보정 (중간 안정점: 편집 모드가 T3Q LLM으로 동작)
3. **report API**: routes_report(toc/content SSE/export) + report_builder(마크다운→hwpx/docx) + 테스트(FakeT3q)
4. **프론트**: 모드 탭 + CriteriaPanel + TocView + ReportView + api.js 재작성 + `npm run build`
5. **e2e·문서화**: 생성 플로우 e2e(FakeT3q), README·limitations 갱신, (인증서 해결 시) 실서버 스모크 = `scripts/probe_t3q.py`

## 8. 테스트 전략

- 단위: httpx `MockTransport`로 RPT-001/002 실측 응답(스트림 절단·오류 라인 포함) 재현
- 통합: FastAPI TestClient — SSE 이벤트 순서(`status→section*→done`), export 산출물 `validate_hwpx` 통과
- 기존 편집 모드 회귀: FakeBackend 기반 테스트 전부 유지 통과 (로그인 제거 보정 외 무수정 목표)
- 실서버: `-m integration` 마커 — 인증서/우회 설정 필요, 계정 불요(현재 무인증 API)

## 9. 리스크

| 리스크 | 대응 |
|---|---|
| cadm-ca.crt ↔ T3Q 서버 발급자 불일치 (§6) | verify 우회 옵션 + 담당자 확인. 코드가 CA 경로 설정형이라 교체만 하면 됨 |
| RPT-002 스트림이 리프 평면 — 목차 트리와 name 매핑 실패 가능 | name 정확 일치 → 정규화 일치(공백·번호) → 순서 폴백, 매핑 실패는 notes |
| 본문 생성 장시간(섹션당 ~20초+) | SSE 진행 표시(기존 status 패턴 재사용), 타임아웃 600s, 섹션 단위 도착 즉시 렌더 |
| 마크다운 표 변환 한계(병합·중첩) | 1차는 단순 그리드만 표로, 실패 시 원문 텍스트 보존 |
| API 무인증 상태(실측) — 향후 토큰 요구 가능 | 요청 헤더 주입 지점을 클라이언트에 일원화 |
