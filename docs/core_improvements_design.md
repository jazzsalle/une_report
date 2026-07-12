# 코어 개선 설계 (A안)

`개선 검토 사항.txt`의 검토 항목 중 외부 의존 없이 코드·테스트로 완결되는
5건의 설계 기록. 브랜치: `feature/core-improvements`.

## A1. 빈 셀 편집 지원 (M1 확장)

**문제**: `apply_edits`는 노드의 기존 `hp:t`를 교체하는 방식이라
`hp:t`가 전혀 없는 노드(한컴에서 만든 실양식의 빈 표 셀)는 조용히
skip된다(`edits.py` "1차 범위 밖"). fill 파이프라인이 정작 채워야 할
셀을 못 채우는 병목이 된다.

**실측 근거** (이번 브랜치에서 재실측): 실양식.hwpx는 노드 194개 중
**108개(56%)** 가 hp:t 없는 빈 셀. 템플릿 01/02/03도 각 7·11·5개.
빈 셀의 지배적 구조는 `tc > subList > p(paraPrIDRef) >
run(charPrIDRef)` — **run은 있고 hp:t만 없다**. 따라서 기존 run에
`hp:t`를 추가하는 경로(아래 1)가 대부분을 커버하며 charPr 복제도
불필요하다.

**설계**:
- `TextNode.elem` 필드 추가 — 노드의 컨테이너 요소 참조
  (table_cell이면 `hp:tc`, body_text면 `hp:p`). `t_elems`가 비어도
  삽입 앵커를 갖게 한다.
- `apply_edits`에서 `t_elems`가 비어 있으면 skip 대신 생성 삽입:
  1. 컨테이너 안(표 내부 제외)에 기존 `hp:run`이 있으면(탭만 있는 run 등)
     그 run에 `hp:t`를 추가한다 — run의 `charPrIDRef`가 그대로 서식이 된다.
  2. run이 없으면 컨테이너의 첫 `hp:p`(셀이면 `subList/p`, 문단이면 자신)에
     `hp:run`+`hp:t`를 생성한다. `charPrIDRef`는 같은 컨테이너 → 같은 섹션
     순으로 가장 가까운 기존 run에서 복제하고, 없으면 속성을 생략한다
     (한컴 기본 서식). 문단의 `paraPrIDRef`는 기존 `hp:p`를 그대로 쓰므로
     문단 서식은 자동 보존된다.
  3. run은 `hp:p`의 마지막 run 뒤(없으면 `hp:linesegarray` 앞)에 삽입해
     요소 순서 규칙을 지킨다.
  4. `hp:p`조차 없는 컨테이너는 종전대로 skip (실측상 희귀 케이스).
- 새 요소의 네임스페이스는 `parse_section`이 반환하던 `t_ns`를 사용
  (기존 반환값 활용 — 시그니처 불변).

## A2. 편집 버전 핀 (M5 계약 확장)

**문제**: 전역 id가 파싱 순번이라, 미리보기 시점과 적용 시점의 문서
버전이 다르면 id가 가리키는 노드가 달라질 수 있다(멀티탭·향후 구조
편집 대비).

**설계**:
- `ChatRequest.base_version: int | None` 추가 (기본 None = 기존 동작).
- `routes_chat`: 문서 컨텍스트 로드 시 `base_version`이 있고 문서의
  `current_version`과 다르면 LLM 호출 전에
  `error {code: "version_conflict"}`로 조기 종료(토큰 낭비 방지).
- `DocumentStore.apply_document_edits(expected_version=...)` 추가 —
  적용 직전 재검사(체크·적용 사이 경합 방어). 불일치 시
  `VersionConflictError`(ValueError 하위) → 라우터가 동일 오류 코드로 매핑.
- `App.vue`: 채팅 payload에 `base_version: doc.version` 동봉.
  `document_updated` 수신 시 이미 `doc.version`을 갱신하므로 추가 상태 불요.

## A3. placeholder 오탐 제외

**문제**(실측 근거): bracket 패턴이 `[그림 1]`·`[표 N]` 캡션 번호와
실양식의 `[ ]`·`[√]` 체크박스를 placeholder로 오탐한다
(`data/real_form_check/재실측_결과.txt` — 실양식 11건 전원 체크박스).

**설계**: `placeholder.py`에 bracket 매칭 후 제외 필터 추가.
- 캡션 번호: `[그림 1]`, `[표 2]`, `[사진 3]`, `[별표 1]`, `[붙임 2]`
- 체크박스: 내용이 공백·체크 기호(√ ✓ ✔ v V x X ○ ●)만인 대괄호
체크박스 표기·해제는 placeholder 채움이 아니라 edit 의도의 텍스트
교체로 처리한다(탐지 목록에서만 제외, 편집 자체는 가능).

## A4. LLM 호출 병합 (M4 지연 절감)

**문제**: 의도 분류 1회 + 본 작업 1회 = 턴당 최소 2회 호출로 체감
지연이 두 배.

**설계**: 분류+응답을 한 프롬프트로 합친 `build_turn_prompt` 추가.
응답 계약: `{"intent", "reply", "edits", "notes"}`.
- `run_turn`(문서 있음): 병합 프롬프트 1회 호출 →
  - `intent=edit` → edits 검증 후 즉시 반환 (**1회로 종결**)
  - `intent=query` → reply 반환 (**1회로 종결**)
  - `intent=fill` → 분류 결과로만 쓰고 기존 청크 fill 파이프라인 실행
    (placeholder 청크 분할·병합은 품질상 유지 — 1+N회)
- JSON 파싱 실패·비정상 intent 시 **기존 분리 경로로 폴백**
  (`_classify_intent` → 분기). 기존 휴리스틱 폴백도 그대로.
- 트레이드오프: query 턴에도 노드 목록이 프롬프트에 실린다(기존엔
  미포함). 대신 문서 참조 질의 품질이 오르고 호출이 절반이 된다.

## A5. fill 분량 지시 + max_tokens

**문제**: 응답이 반 페이지에서 끊기는 증상. 로컬 측 원인 두 가지 —
(1) `_chat_body`가 생성 길이 파라미터를 안 보내 서버 기본값(512~1024
추정)에 걸림, (2) 프롬프트 예시가 "한 줄 답변"이라 LLM이 짧은 출력을
모방.

**설계**:
- `config.LLM_MAX_TOKENS`(기본 4096, `0`이면 미전송) 추가.
  `UniRagClient._chat_body`에서 opts에 없을 때만 `max_tokens`로 동봉
  (서버가 무시해도 무해, passthrough 협의는 C안 몫).
- 프롬프트: fill 규칙에 명시적 분량 지시 추가 — "문단 placeholder의
  new_text는 공문서 문체 3~5문장, 표 셀은 1~2문장". `EDIT_JSON_EXAMPLE`의
  new_text 예시를 실제 분량 수준으로 교체.

## B. 실양식 인지 실패 대응 보완 설계

출처: `ouputs/hwpx_양식인지실패_원인분석_개선안.md` (서식1 사업계획서 실측).
전제: UNI 서버 협의 불가 → 전부 클라이언트 측 작업. A안과의 중복 정리 —
**원인 2(빈 셀 쓰기)는 A1로, 원인 4의 응답 잘림 대응(max_tokens·분량
지시·실물 분량 예시)은 A5로 이미 해결**. 남은 단기 과제 3건을 구현한다.
중기 과제(2단계 필드맵 파이프라인 §3-5, create 의도 배선 §3-6, 줄 단위
프로토콜 §4.2-2)는 이번 범위에서 제외하고 후속으로 미룬다.

### B1. 스타일 기반 가이드 감지 (§3-2)

**실측**: 서식1은 채울 곳을 "파란 기울임체 작성 가이드"(charPr 61종,
run 264개·5,891자)로 표시한다. regex placeholder는 36건만 잡혀 채움
대상의 97%가 보이지 않았다.

- `app/core/hwpx/styles.py` 신설: `guide_char_pr_ids(header_path)` —
  header.xml의 charPr 중 **기울임(`<hh:italic>` 자손) + 파란 계열
  textColor(B≥128, B>R+40, B>G+40)** 인 id 집합. 서식1 61개 적중,
  일반 문서(실양식·템플릿 3종) 0개 오탐 — 실측 검증 완료.
- `package.find_header_file(extract_dir)` 추가 (Contents/header.xml).
- `TextNode.guide_text` 필드 추가. `parse_section(..., guide_char_ids=None)`
  옵션 인자로 가이드 run의 텍스트만 이어붙여 저장 (기본값 None = 기존 동작).
- `DocumentStore.get_placeholders`: regex 히트(kind="pattern")에 더해
  guide_text 있는 노드를 kind="guide", token=가이드 문구로 추가.
  가이드 문구 자체가 작성 지시("…500자 내외 작성")이므로 fill 프롬프트
  재료로 그대로 쓴다. fill 규칙에 "가이드 문구는 지시를 따라 실제 내용으로
  대체(가이드 원문 제거)"를 명시 — 양식의 "가이드 삭제 후 제출" 요구를
  채움과 동시에 충족하고, 채움 후 get_placeholders 재호출이 가이드 잔존
  검사를 겸한다(§3-5 3단계의 선반영).

### B2. 표 구조 노출 (§3-3)

- `get_nodes`: 셀 노드에 `table_idx/row/col/row_span/col_span` 포함.
- `prompts.format_nodes`: 평문 나열 → 본문 문단 + 표별 그리드
  (`(행,열) id: 텍스트`, 빈 셀은 `(빈 칸)`) 직렬화. 같은 행·열의 라벨이
  프롬프트상 인접해 "이 빈 셀이 어느 라벨의 값 칸인지"가 드러난다.

### B3. 프롬프트 규모 제어 (§3-4)

- edit·query(병합 포함) 프롬프트: selection이 없으면 **텍스트 없는 노드
  제외** (서식1 기준 5,386 → 약 1,700노드). selection이 있으면 사용자가
  빈 셀을 직접 지목한 것이므로 전부 유지.
- fill 대상 확장·축소를 동시에: **placeholder/guide 노드 + 그 노드가 속한
  표의 빈 셀**만 대상 (기존: placeholder 노드만 → 빈 셀 전부 누락 /
  placeholder 0이면 전체 노드 → 180회 호출 폭주).
- fill 청크: "노드 30개" → **표 단위 우선**(같은 표는 같은 청크 흐름,
  초과 시 행 순서 유지한 채 분할, 본문은 기존 크기 유지). 청크당 기대
  출력이 작아져 서버 상한(§4.2-1) 아래로 들어간다.

### B4. 실양식 회귀 테스트 (§3-8)

서식1을 픽스처로 하드 게이트: 노드 5,386 · hp:t 없는 노드 3,687 ·
가이드 charPr 61종 · 빈 셀 apply_edits 성공. 파일 부재 시 skip
(대용량 실양식은 저장소 커밋 대상 아님).

## 테스트 계획

- A1: 실양식.hwpx의 hp:t 없는 셀 실측 + 합성 XML(빈 셀: run 없음/탭만
  run/subList만) 라운드트립 — 삽입 후 validate·재파싱 반영 확인.
- A2: base_version 불일치 → version_conflict 이벤트, apply 직전 불일치
  → VersionConflictError.
- A3: 캡션·체크박스 제외, 정상 `[기관명]` 유지.
- A4: FakeBackend로 병합 응답 1회 종결(edit/query), fill 위임, 깨진
  JSON → 분리 폴백 경로 검증(호출 횟수 단언).
- A5: _chat_body에 max_tokens 포함·opts 우선·0이면 미전송.
- 기존 146개 회귀 전체 통과.
