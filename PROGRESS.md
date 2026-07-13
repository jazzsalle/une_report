# PROGRESS

## Last updated
2026-07-13

## Current goal
LLM 대화 기반으로 hwpx 문서를 생성·편집하고 결과물(hwpx·docx)을 즉시 출력하는 독립형 도구 개발.
(설계 원본: `개발 배경 및 목적.txt`, Phase 정의: `CLAUDE.md`, 합격 기준: `evaluation_criteria.md`)
Phase 1~7은 이전 세션에서 완료(커밋 이력 참조). 현재는 **개선 브랜치 작업 중**.

## 현재 브랜치
`feature/core-improvements` — origin에 푸시됨 (main 미병합).
설계 기록: `docs/core_improvements_design.md` (A·B·C 전체), 개선 출처:
루트 `개선 검토 사항.txt`(A안)·`ouputs/hwpx_양식인지실패_원인분석_개선안.md`(B안).

## Done this session (2026-07-12~13)
- **A안 — 코어 개선 5건** (커밋 4ab08e0):
  - A1 빈 셀 편집: `TextNode.elem` 앵커 + `apply_edits`가 hp:t 없는 노드에 hp:run/hp:t 생성 삽입.
    실측 근거: 실양식.hwpx 194노드 중 108개(56%)가 hp:t 없음, 빈 셀은 `run(charPrIDRef)`만 있고 t 없음이 지배적
  - A2 버전 핀: `ChatRequest.base_version` → 불일치 시 LLM 호출 전 `version_conflict` SSE 오류,
    `apply_document_edits(expected_version)` 재검사, App.vue 자동 미리보기 복구
  - A3 placeholder 오탐 제외: `[그림 N]`·`[표 N]` 캡션, `[ ]`·`[√]` 체크박스 (실양식 11건 전원 체크박스 오탐이었음)
  - A4 LLM 호출 병합: `build_turn_prompt`로 분류+응답 1회 수신({intent,reply,edits,notes}),
    JSON 실패·무효 intent 시 기존 분리 경로(분류→작업) 폴백. edit·query 턴 2회→1회
  - A5 fill 분량 지시(문단 3~5문장·셀 1~2문장) + 예시 실물 분량화 + `LLM_MAX_TOKENS`(기본 4096) 동봉
- **B안 — 실양식 인지 실패 대응** (커밋 97dba7b, 출처: ouputs 분석 문서. UNI 서버 협의 불가 전제):
  - B1 가이드 감지: `app/core/hwpx/styles.py` 신설 — header.xml charPr "기울임+파랑(B≥128, B>R+40, B>G+40)" 판정.
    서식1 61종 적중·타 문서 4종 오탐 0 실측. `TextNode.guide_text`, `get_placeholders`에 kind=guide(작성 지시문)
  - B2 표 구조 노출: `get_nodes`에 table_idx/row/col/span, 프롬프트를 표 그리드("(행,열) id: 텍스트", 빈 칸 표기)로 직렬화
  - B3 규모 제어: edit·query 프롬프트에서 빈 노드 제외(selection 있으면 전부 유지), 표 불분할 greedy 청킹
  - B4 회귀 게이트: `tests/test_real_form_regression.py` — 서식1 고정치(노드 5,386·빈 3,687·가이드 charPr 61) 하드 게이트
- **UNI RAG 500 원인 실측·대응** (커밋 15c52a6):
  - 실측: /chat/ 쿼리 **48k자 OK / 54k자부터 HTTP 500** (max_tokens 필드는 무관 — A/B로 배제)
  - `PROMPT_CHAR_BUDGET`(기본 30k)로 노드 목록 예산 집행, 초과분 생략 + notes 안내("미리보기에서 선택 후 요청")
  - 서식1 실서버 e2e 통과 확인 (914노드 생략 안내와 함께 정상 응답)
- **C안 — fill을 구조 인식 전량 재작성으로 재설계** (커밋 ff04fd4, 오류 캡처 `ouputs/오류사항 화면캡쳐.png` 대응):
  - 배경: 스텁 단어 템플릿(title·표제목·표내용)은 표식이 없어 종전 fill이 1개 노드만 채움
  - fill 대상 = 문서 전체 노드(빈 셀 포함). 기존 텍스트는 구조 힌트(제목 자리·헤딩 기호·표 머리글·라벨)로 쓰고
    새 내용을 배치, 무관한 옛 본문은 `new_text:""`로 삭제. 라벨 유지/대체는 LLM 판단(사용자 결정)
  - `FILL_NODE_LIMIT`(기본 300) 안전판: 초과 시 표식·가이드 중심 축소(+notes), 표식 없으면 앞쪽 상한만
  - 실서버 재현 검증: 같은 템플릿+호우 매뉴얼 내용 → 종전 1개 → **31개 생성·25개 적용**, 스텁 전부 소거
- 테스트: 146 → **194 passed** (pytest, integration 2 deselected)
- **문장 겹침 버그 수정** (2026-07-13, `ouputs/문장겹침_원인분석_개선안.md`):
  - 원인: `apply_edits`가 텍스트만 교체하고 편집 문단의 `hp:linesegarray`(한컴 줄배치 캐시)를
    남겨둠 → 한컴이 옛 텍스트(1줄) 기준 배치를 재사용해 긴 새 텍스트가 겹쳐 렌더
    (`ouputs/응답결과.hwpx` 실측: 스텁 템플릿 캐시 33개 전부 잔존)
  - 수정: `edits.py` — 편집(교체·빈 셀 삽입)된 노드의 소속 hp:p마다 linesegarray 제거,
    미편집 문단 캐시는 보존. parse_section의 parent_map 활용 (자체 구현, 오픈소스 미복사)
  - 테스트 2건 추가(캐시 주입 후 제거·보존 검증) → **196 passed**
  - 기존 산출물 복구본: `ouputs/응답결과_겹침수정.hwpx` (전 문단 캐시 제거, validate 통과) — 한컴 육안 확인 대기
- 실측 프로브 이력: scratchpad에서 수행(저장소 외). UNI RAG passthrough — max_tokens/max_new_tokens 모두 200 수용(효과는 미검증)

## In progress
- 없음 (모든 변경 커밋·푸시 완료)

## Next steps
1. (권장) `feature/core-improvements` → main PR 생성·병합 (https://github.com/jazzsalle/une_report/pull/new/feature/core-improvements)
2. 분석 문서의 중기 과제: 2단계 필드맵 파이프라인(§3-5), create 의도 배선(§3-6, `build_hwpx` 연결), 줄 단위 프로토콜·검증 루프(§4.2)
3. B안 잔여(사용자 미선택分): 보안·위생 — app_tokens TTL·로그아웃, rag_jwt 평문 limitations 명시, web/dist gitignore
4. max_tokens 실효성 검증: 서버가 필드를 수용은 하나 생성 길이에 실제 반영되는지 장문 질의로 A/B
5. 한컴오피스 육안 확인: 재작성 fill 산출물(빈 셀 삽입 포함) 렌더링 점검 (`docs/hwpx_validation.md` §3)

## Blockers
- 없음. (UNI 담당자 협의 불가 상태 — guided_json 등 서버 측 개선은 보류, 전부 클라이언트 측으로 우회 중)

## 로컬 전용 파일 (커밋 안 됨 — 회사 PC에는 없음)
- `개발 배경 및 목적.txt`(gitignore, 실계정 미포함 — 사용자 확인), `.env`(테스트 계정), `seed_base.hwpx`, `실양식2.hwpx`(루트), `ouputs/실양식*.hwpx`(루트 사본)
- 커밋된 실측 픽스처: `ouputs/[서식1] 사업계획서(신청용).hwpx`(회귀 테스트가 사용, 없으면 skip), `ouputs/오류사항 화면캡쳐.png`, `개선 검토 사항.txt`

## How to run
- 백엔드: `.venv/Scripts/python -m uvicorn app.main:app --port 8080` / 테스트: `.venv/Scripts/python -m pytest`
- 회사 PC 최초 셋업: clone → `git checkout feature/core-improvements` → venv 생성·`pip install -r requirements.txt` → `.env.example`을 `.env`로 복사해 테스트 계정 기입 → (UI 수정 시) `cd web && npm install && npm run build`
