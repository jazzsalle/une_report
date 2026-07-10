# 오픈소스·자료 조사 분석 보고서 (Phase 1)

- 작성일: 2026-07-11
- 목적: "LLM 대화 기반 hwpx 문서 생성·편집 도구"(독립형) 개발을 위해, 참조 오픈소스와 연동 API를 분석하고 채택/참고/제외를 판단한다.
- 조사 방법: 각 레포·API를 병렬 조사(레포 클론 및 소스 직접 확인 포함). 스타 수·버전은 조사일 기준.

---

## 1. 핵심 결론 요약

1. **목표 기능의 실제 구현체를 찾았다.** 공식 문서의 "대화 한번으로 문서 수정, 결과물(hwpx·docx) 즉시 출력" 기능은 process-gpt 본체도, completion 백엔드도 아닌 **`process-gpt-office-mcp` 서브모듈**(FastMCP HTTP 서버, 포트 1192)이 구현한다.
2. office-mcp는 **외부 hwpx 라이브러리 없이 Python 표준 라이브러리(zipfile + ElementTree)만으로** HWPX(zip+OWPML XML)를 직접 조작한다. 의존성이 가벼워 **독립형 도구로 떼어내기에 최적**이다.
3. 핵심 설계 패턴은 **"HWPX ↔ HTML 왕복 편집"**: hwpx→HTML 변환 시 문단·셀에 순차 `data-id`를 주입하고, LLM은 `edits: [{id, new_text}]` 형태의 부분 수정만 반환하며, 저장 시 HTML 변경분을 원본 XML 노드에 매핑해 재압축한다.
4. LLM 대화는 **UNI RAG System**(`http://221.147.100.161:8000`)의 `POST /chat/`(Bearer JWT)로 붙일 수 있고, 인증은 `POST /auth/login`(UNE 계정)으로 해결된다. 이 서버는 이미 `.hwpx` 파일 생성 기능(`/chat/json` + `/chat/files/{id}`)도 내장하고 있으나 SOP 매뉴얼 포맷 특화라 범용 편집에는 `/chat/`이 적합하다.
5. hwpx 쓰기를 처음부터 직접 구현할 필요는 없다. **office-mcp 코드 응용(1순위) + python-hwpx 보조(2순위)** 조합이 타당하다. rhwp·hwpConverter는 코어 채택 대상이 아니라 참고 자료다.

---

## 2. Process-GPT 생태계 분석

### 2.1 uengine-oss/process-gpt (본체, 모노레포)

- **역할**: BPMN 표준 + AI 에이전트 결합 프로세스 자동화 플랫폼의 우산 레포. 실제 코드는 15개 git 서브모듈(마이크로서비스)이고, 본체는 인프라 구성(docker-compose, k8s)·통합 실행 스크립트·문서 담당. MIT 라이선스.
- **인프라**: Supabase(Postgres/Auth/Storage/벡터DB), Neo4j, LiteLLM 프록시, Nginx 게이트웨이(8088), 프론트(8080), completion(8000), **office-mcp(1192)**.
- **서브모듈 중 주목 대상**: `office-mcp`(문서 생성 — 핵심), `frontend`(process-gpt-vue3), `completion`(LangChain 채팅/폼), `memento`(RAG).

### 2.2 "대화 한번으로 문서 수정 → hwpx·docx 즉시 출력" 기능의 동작 방식

공식 문서(docs.process-gpt.io/ko/advanced-features/voice-chat/)가 광고하는 기능:
HWPX 양식(복잡한 표·서식) 빈칸 자동 채움, RAG 기반 자율 데이터 결합, 채팅 자연어 지시("과제명을 ~로 수정해 줘") 즉시 반영, 부분 드래그 수정, 우측 미리보기.

실제 구현(`process-gpt-office-mcp` 소스 확인 기반):

**MCP 툴 8개**: `list_reference_documents`, `generate_hwpx`, `edit_hwpx_page_html`, `save_hwpx_from_html`, `generate_docx`, `edit_docx_page_html`, `save_docx_from_html`, `generate_slides`

**① 생성 파이프라인** (`office_mcp/formats/hwpx/runner.py`, `process_hwpx_file()`):

```
템플릿 hwpx 다운로드 → 압축 해제(extract_hwpx) → section XML 파싱(parse_section)
→ 의미론적 청킹(10~30노드, 같은 표의 셀은 Union-Find로 동일 청크 강제)
→ [선택] Playwright로 청크 HTML 스크린샷 → LLM 비전으로 서식 이해
→ LLM 5단계 호출: 청크 분석 → RAG 쿼리 생성 → 이미지 참조 판단/선택 → 채움 데이터 생성
→ apply_fills()로 원본 XML 트리 in-place 수정(스타일 태그 보존)
→ repack_hwpx()로 원본 압축 메타·파일 순서 유지 재압축
→ 미채움 placeholder 정규식 검증 + 출처 사이드카 JSON 생성
```

**② hwpx→HTML 변환** (`hwpx_to_html.py`): 표준 라이브러리만 사용. `hp:p`/`hp:tbl`/`hp:pic` 파싱, charPr/paraPr→CSS 매핑(HWP 단위 1/7200inch→px, BGR→RGB), 이미지 base64 data URI, lineseg 기반 페이지 분할(`.page` div), **문단·셀에 순차 `data-id` 주입**. 이 HTML이 프론트 미리보기의 소스.

**③ 대화형 수정** (`hwpx_edit.py`): 수정된 HTML(또는 LLM 수정 제안)에서 `data-id`별 변경 추출 → 원본 XML 노드 매핑 → 텍스트 교체/표 삭제 등 수정 → 재압축.

**④ docx**: python-docx 사용 (스키마 추출 → LLM 채움 → docx+HTML 동시 생성).

**사용자 흐름**(일부 추정): 프론트 채팅 요청 → AI 에이전트가 office-mcp 툴 호출 → 결과 hwpx/HTML을 Storage 업로드 → 프론트 우측 패널 미리보기 → 드래그/자연어 수정 시 `edit_*`/`save_*` 툴 호출.

**판단: ★채택.** `office_mcp/formats/hwpx/`(runner, hwpx_to_html, hwpx_edit) + `office_mcp/core/`(parser, filler, chunker, style_mapper, table_analyzer, xml_utils, html_edit 등)를 응용한다. Supabase 업로드·memento RAG·Tavily·Gemini 이미지·Playwright 비전은 선택적 의존이므로 분리 시 제거/스텁 처리한다.
**주의**: 변환기가 범용 완전체는 아님 — 지원 요소가 hp:p/tbl/pic/lineseg 중심, 스타일은 charPr/paraPr/borderFill 수준. 수식·도형·각주 등 복잡 개체는 검증 필요.

### 2.3 uengine-oss/process-gpt-completion (백엔드)

- **역할**: 문서 편집기가 아니라 **BPMN 프로세스 실행 엔진**. 자연어 답변을 LLM으로 판정해 폼 필드 값을 추출하고 다음 액티비티를 결정.
- **스택**: Python + FastAPI + LangChain 0.3(LCEL), `llm_factory.py`의 ChatOpenAI(LiteLLM 프록시 경유), Supabase 멀티테넌시, API 서버 + 폴링 워커 이원화.
- **핵심 발견: 이 레포에는 hwpx/docx 파일을 생성·수정·출력하는 코드가 없다.** 문서 입력 파싱조차 Upstage 유료 API(document-digitization)에 위임하는 읽기 전용(`polling_service/document_parser.py`, `.hwpx` 지원). "문서"의 실체는 `form_def` 테이블의 HTML 폼 + LLM이 채운 JSON 값.

**판단: 부분 참고.**
- 참고 가치: `CustomJsonOutputParser`(LLM JSON 출력 깨짐 복구), `FieldMapping(key,name,value)` + `fields_json` 패턴("문서 = 필드 스키마 + 값" 모델), `features/process_chat/`의 OpenAI 호환 SSE 스트리밍 구현, `llm_factory.py`(환경변수 기반 LLM 추상화).
- 제외: BPMN 엔진 전체, Supabase 멀티테넌시, 폴링 이원화 구조(즉시 출력 목표와 상충), Upstage 의존.

### 2.4 uengine-oss/process-gpt-vue3 (프론트엔드)

- **역할**: Process-GPT의 Vue3 프론트. 단독 동작 불가(백엔드 마이크로서비스 전제). Vue 3.2 + Vuetify 3.4 + Pinia/Vuex 혼용, 상용 어드민 템플릿 기반 3,000+ 파일의 거대 코드베이스.
- **문서 UI 구조**: 좌측 채팅 + 우측 리사이즈형 아티팩트 탭 패널(`ArtifactPanel.vue`), 범용 뷰어 `HwpxViewer.vue`(1,812줄). **hwpx를 브라우저에서 파싱하지 않고 서버 변환 HTML만 렌더** — `fetch(htmlUrl)` → DOMParser → CSS 스코핑 → contenteditable `v-html`.
- **편집 흐름**: 뷰어에서 `[data-id]` 요소 선택 + 지시문 → office-mcp에 MCP JSON-RPC 직접 호출(`edit_hwpx_page_html {hwpx_url, page_number, instruction}`) → 응답 `edits:[{id,new_text}]`를 DOM에 부분 적용 + 하이라이트. 편집 중엔 HTML 상태로 유지하다가 **다운로드 시점에만** `save_hwpx_from_html {hwpx_url, edited_html}` → 서버가 hwpx 재패키징 → `{file_url, html_url}` 반환(지연 저장).
- **최소 구현 예시**: `src/components/ui/field/HwpxEditorDialog.vue`(약 200줄) — 사실상 "독립형 hwpx 편집기"의 축소판.

**판단: 패턴 채택, 코드 전체는 제외.**
- 채택 패턴: ① 서버 변환 HTML + 브라우저는 HTML만 렌더, ② office-mcp 도구 계약(edit/save 시그니처), ③ `.page` + `data-id` 앵커 부분 편집, ④ 채팅+아티팩트 패널 UX(생성 시 자동 오픈, raw 링크를 파일 카드로 치환), ⑤ SSE 스트리밍 파서.
- 제외: 레포 전체(거대·강결합). 주의점: 원본은 LLM 산출 HTML을 sanitize 없이 `v-html` 렌더 — 우리는 DOMPurify 또는 iframe sandbox 필수.

---

## 3. hwpx 처리 오픈소스 분석

### 3.1 edwardkim/rhwp — 참고

- Rust+WASM, MIT, 스타 ~3.5k, 매우 활발(v0.7.17, 2026-06). HWP 5.0/HWPX 파싱·렌더링(SVG/PNG/PDF)·웹 에디터·CLI.
- HWPX 직렬화 모듈(`src/serializer/hwpx/`)이 있으나 **README에 "HWPX 출처 문서 저장은 비활성화"라고 명시** — 3.5k 스타 규모로도 완전한 hwpx 쓰기 호환성 확보가 어렵다는 실증.
- **판단: 참고.** hwpx 직렬화의 실무 함정(이미지 속성, 네임스페이스 등) 사례집으로 가치. Rust/WASM 스택 + 렌더러 중심 설계라 코어 채택은 미스매치.

### 3.2 vsdn/hwpConverter — 참고(제외에 가까움)

- Java, Apache-2.0, 스타 19, 커밋 5개(코드 덤프형). HWP↔HWPX↔ODT↔Markdown 변환 CLI.
- hwpx 생성의 실질 엔진은 외부(dogfoot hwpxlib) 의존. 문단 단위 편집 API가 아닌 파일 일괄 변환기.
- **판단: 참고.** `mdlib/`의 Markdown→HWPX 변환 전략은 "LLM 마크다운 출력→hwpx" 경로 참고용. 지속성 리스크 커서 채택 부적합.

### 3.3 대안 라이브러리 비교

| 라이브러리 | 스택/라이선스 | 상태 | 평가 |
|---|---|---|---|
| **airmang/python-hwpx** | 순수 Python, Apache-2.0, PyPI | 스타 90, v2.24.0, 활발 | **보조 채택 후보.** 한컴 설치 불필요, 빈 문서부터 빌더 패턴 생성, 표/이미지/스타일, XSD 검증. 자매 프로젝트 hwpx-mcp-server·hwpx-skill 존재("LLM으로 hwpx"와 목적 일치). 한계: 도형/컨트롤 미완, 개인 유지보수(포크 대비) |
| neolord0/hwpxlib | Java, Apache-2.0 | 스타 179, 활발 | JVM 스택이라면 사실상 표준. 우리는 Python 스택이라 미채택 |
| hancom-io/hwpx-owpml-model | C++, Apache-2.0 | 방치 | OWPML 스펙 레퍼런스로만 참고 |
| martiniifun/pyhwpx | Python+COM, MIT | 활발 | 한컴오피스 설치 필수·Windows 전용 → 서버 도구 부적합. 단, **최종 출력물 렌더링 검증**(한컴 자동화)에는 활용 가능 |

### 3.4 직접 구현 vs 응용 결론

**직접 구현 불필요.** hwpx는 zip+OWPML XML이라 진입장벽이 낮아 보이지만 완전한 쓰기 호환성은 어렵다(rhwp 실증). 전략:

- **템플릿 기반 편집·채움(주 시나리오)**: office-mcp 방식 채택 — 원본 hwpx의 XML을 최소 수정 후 재압축(스타일·압축 메타 보존). 호환성 리스크가 가장 낮다.
- **무(無)템플릿 신규 생성(보조 시나리오)**: python-hwpx 빌더 활용.
- 공통 한계(수식·도형·암호화·정밀 조판)는 어떤 라이브러리도 동일 → **한컴오피스 렌더링 검증 절차**를 파이프라인에 둔다.

---

## 4. UNI RAG System API 분석 (LLM 연동)

서버: `http://221.147.100.161:8000` (FastAPI, OpenAPI 취득 성공, `/health` 정상).

### 4.1 대화 엔드포인트

**① `POST /chat/` — 메인 채팅 (Bearer JWT 필요) → 우리 도구의 1순위**

```json
{
  "query": "(필수) 사용자 질문",
  "model_key": "qwen3-coder-next",
  "history": [{"role":"user|assistant","content":"..."}],
  "stream": true,
  "top_k": 5,
  "session_id": "(선택) 서버측 대화 저장",
  "thinking": false
}
```

- 히스토리 관리 2방식: (a) `history` 배열 직접 전달(stateless, 구현 최소 — **채택**), (b) `session_id` + `/sessions/` CRUD(서버 저장).
- stream=true 시 SSE, false 시 JSON. **응답 바디 스키마가 OpenAPI에 미선언 → 개발 착수 시 유효 계정으로 1회 실호출해 필드 확인 필요.**

**② `POST /chat/json` + `GET /chat/files/{file_id}` — 문서 생성 파이프라인 (인증 불필요, 백엔드-투-백엔드 전용 명시)**

- RAG 검색 → 리랭킹 → LLM 스트리밍 → SOP JSON `compns` 요소 단위 SSE 전송. 완료 후 `/chat/files/{id}`로 **`.hwpx`**·`.json`·`.svg` 다운로드.
- 즉 **UNI RAG 서버가 이미 hwpx 생성 기능을 내장.** 단 SOP 매뉴얼 포맷 특화 → 범용 문서 편집 대화에는 부적합. 재난 계획서가 SOP 형태와 겹치는 부분이 있으면 활용 검토 가치 있음.

### 4.2 인증

- `POST /auth/login` 바디 `{"account","password"}`(UNE 계정) → 자체 JWT → `Authorization: Bearer`. hr.unes.kr을 직접 칠 필요 없이 RAG 서버가 위임 인증.
- hr.unes.kr 외부 연동 API(참고): `POST /api/external/auth`(JWT 24h), `POST /api/external/verify`, `X-API-Key`(전 직원 목록 동기화용 — 우리 용도엔 불필요).
- 토큰 만료 검증 엔드포인트가 RAG 서버엔 없음 → **401 응답 시 재로그인** 처리.

### 4.3 모델 및 리스크

- 실측 가용 모델: **`qwen3-coder-next`만 available=true** (exaone-32b, qwen3.5-35b는 false). 전부 로컬 vLLM.
- 리스크: ① 코딩 특화 모델의 한국어 공문서 작성 품질 미검증, ② 단일 모델 의존, ③ 서버가 평문 HTTP(사내망 전제라도 계정/비번 평문 전송 유의), ④ 응답 스키마 미문서화.

---

## 5. 종합: 채택/참고/제외 매트릭스

| 대상 | 판단 | 활용 내용 |
|---|---|---|
| process-gpt-office-mcp | ★ 채택 | hwpx↔HTML 왕복 편집 코어(runner/hwpx_to_html/hwpx_edit + core 모듈), MCP 도구 계약, 재압축·placeholder 검증 패턴 |
| process-gpt-vue3 | 패턴 채택 | 채팅+아티팩트 패널 UX, data-id 부분 편집 적용, 지연 저장, SSE 파서. 코드 전체는 제외 |
| process-gpt-completion | 부분 참고 | LLM JSON 복구 파서, 필드 스키마+값 모델, SSE 응답 포맷. 문서 생성 코드 없음 |
| process-gpt 본체 | 제외 | BPMN 엔진·인프라(Supabase/Neo4j/LiteLLM/k8s)는 독립형 도구에 과함 |
| python-hwpx | 보조 채택 | 무템플릿 신규 hwpx 생성, XSD 검증 |
| rhwp | 참고 | hwpx 직렬화 함정 사례집 (MIT) |
| hwpConverter | 참고 | md→hwpx 변환 전략 (Apache-2.0) |
| UNI RAG `/chat/` | 채택 | LLM 대화 엔진 (Bearer JWT, history 배열 방식) |
| UNI RAG `/chat/json`+`/chat/files` | 조건부 활용 | SOP형 문서 자동 생성이 필요할 때 |
| Upstage document-digitization | 제외 | 유료 외부 API. hwpx 읽기는 자체 파서로 대체 |

## 6. 미확인·후속 확인 필요 사항

1. UNI RAG `/chat/` 실제 응답 바디 스키마 (stream=true/false 각각) — 개발 착수 시 실호출 확인.
2. `qwen3-coder-next`의 한국어 문서 편집 지시 이행 품질 — Phase 5에서 프롬프트 실험 필요.
3. office-mcp hwpx 변환기의 실패 케이스 범위(수식·도형·각주) — 실제 재난 계획서 양식으로 실측 필요.
4. office-mcp 레포의 라이선스 명시 여부 — 본체(process-gpt)는 MIT이나 서브모듈 개별 라이선스 재확인 권장.
5. 데모 영상(YouTube)은 직접 시청 불가 — 공식 문서 서술로 갈음.
