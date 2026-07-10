# hwpx 출력물 검증 절차 (Phase 4)

- 작성일: 2026-07-11
- 목적: M1(hwpx 코어)이 생성·편집한 hwpx가 유효한 문서인지 확인하는 절차를 정의한다.
  자동 검증(`validate_hwpx`) → 단위 테스트 불변식 → 한컴오피스 육안 확인의 3단계로 구성하며,
  실양식(재난 계획서) 실측 결과와 입수 시 재실측 체크리스트를 함께 기록한다.

---

## 1. 자동 검증 — `validate_hwpx`가 확인하는 항목

`app.core.hwpx.validate_hwpx(hwpx_path) -> ValidationResult(ok, errors, checked)`
(구현: `app/core/hwpx/package.py`)

| # | 검사 항목 | 실패 시 의미 |
|---|---|---|
| 1 | zip 아카이브로 열림 (`zip_open`) | hwpx가 아예 zip이 아님(손상·비정상 파일) |
| 2 | `mimetype` 엔트리 존재 | OCF 컨테이너 식별 불가 — 한컴오피스가 형식을 인식 못할 수 있음 |
| 3 | `Contents/header.xml` 존재 | 스타일·글꼴 정의 유실 |
| 4 | `Contents/section*.xml` 1개 이상 존재 | 본문이 없음 |
| 5 | `Contents/content.hpf` 존재 | 패키지 매니페스트 유실 |
| 6 | 위 XML들(header·모든 section·content.hpf)이 전부 파스 가능 | 편집 과정에서 XML이 깨짐(태그 불일치, 인코딩 오류 등) |

추가로 재압축 계층(`repack_hwpx`)이 구조적으로 보장하는 사항 — 테스트로 검증됨:

- `mimetype`이 **항상 첫 엔트리 + 무압축(ZIP_STORED)** (OCF 규약)
- 원본 zip의 **엔트리 순서·엔트리별 압축 방식 보존** (`extract_hwpx`가 반환한 메타 재사용)
- `META-INF`의 서명·암호화 관련 엔트리는 재압축에서 제외 (편집으로 서명이 무효가 되므로)
- 직렬화 시 원본 네임스페이스 프리픽스(hp/hs/hc 등) 보존
  (`xml_utils.register_namespaces` — 미등록 시 `ns0:` 프리픽스가 생겨 한컴오피스에서 열리지 않음)

한계: `validate_hwpx`는 **패키지·XML 정합성**만 본다. OWPML 스키마 준수 여부(요소 순서,
속성 유효성)나 시각적 렌더링 품질은 검사하지 않으므로 아래 3단계 육안 확인으로 보완한다.

## 2. 단위 테스트 — 자동 검증 스위트 (`tests/`)

`.venv/Scripts/python -m pytest` (저장소 루트) — Phase 4 기준 53개 전부 통과 (2026-07-11).

| 테스트 파일 | 검증 내용 |
|---|---|
| `tests/test_hwpx_package.py` | extract→repack 왕복 시 엔트리 목록·순서·압축 방식·내용 바이트 보존, mimetype 첫 엔트리·STORED, `validate_hwpx` 합격(정상 3종)·불합격(빈 zip, 비zip, 섹션 없는 zip), 섹션 파일 숫자 정렬 |
| `tests/test_hwpx_parser_html.py` | 노드 수·타입·표 셀 좌표(table_idx/row/col/span), **핵심 불변식: HTML `data-id` 집합 == parse_section 전역 id 집합**(중복 없음, node_count 일치), `inject_ids=False`·`split_pages` 옵션 |
| `tests/test_hwpx_edits.py` | `apply_edits` → `validate_hwpx` 통과 → 재파싱으로 새 텍스트 반영 확인, 동일 텍스트/미존재 id skip, `"p-0012"` 정규화, **편집하지 않은 노드의 원문 불변** |
| `tests/test_hwpx_placeholder.py` | placeholder 4유형(bracket/angle/date_stub/filler) 탐지, 편집 후 `verify_output`의 잔존/해소 구분, `chunk_nodes` 표 불분할 |

테스트 픽스처는 두 계열이다 (`tests/conftest.py`):

1. **python-hwpx 동봉 실물 샘플** — `import hwpx; Path(hwpx.__file__).parent` 아래에서
   동적으로 탐색(경로 하드코딩 없음): `data/Skeleton.hwpx`, `conformance/corpus/report_table.hwpx`
2. **builder 생성 양식** — `build_hwpx(placeholders_demo=True)`로 placeholder 6종이 포함된
   임시 양식을 매 테스트마다 새로 생성

## 3. 한컴오피스 / 한글뷰어 육안 확인 절차

자동 검증 통과 후, 결과물을 실제 뷰어에서 여는 최종 확인. 한컴오피스(정품) 또는
무료 한글뷰어(https://www.hancom.com 다운로드)로 수행한다.

> 참고: 본 개발 환경에는 한컴오피스가 설치되어 있지 않아 이 절차는 문서화만 하고,
> 실제 수행은 한컴오피스가 있는 검수 환경에서 진행한다(Phase 7 통합 테스트 항목).

절차:

1. 검증 대상 hwpx를 한컴오피스/한글뷰어로 연다 (더블클릭 또는 파일 > 불러오기).
2. **열림 여부**: "파일이 손상되었습니다", "지원하지 않는 형식" 등의 오류 없이 열리는가.
   - 열리지 않으면 최우선 의심: 네임스페이스 프리픽스 훼손(`ns0:`), mimetype 규약 위반.
3. **깨짐 확인 체크리스트**:
   - [ ] 한글 텍스트가 모두 정상 표기되는가 (인코딩 깨짐 `占쏙옙`·`?` 없음)
   - [ ] 편집한 노드에 새 텍스트가 들어가 있고, 옛 텍스트 조각이 남아 있지 않은가
   - [ ] 편집한 노드의 **글자 서식**(글꼴·크기·굵기·색)이 원본과 동일하게 유지되는가
   - [ ] 편집하지 않은 문단·표가 원본과 동일한가 (원본 파일과 나란히 놓고 대조)
   - [ ] 표 구조가 유지되는가 (행·열 수, 셀 병합, 테두리, 셀 배경색)
   - [ ] 이미지·머리말/꼬리말·쪽 번호가 유실되지 않았는가
   - [ ] 페이지 수·페이지 나눔 위치가 원본과 동일한가
   - [ ] 저장(다른 이름으로 저장)이 정상 동작하는가 — 한컴이 재저장 가능한 문서인가
4. 웹 미리보기(`hwpx_to_html` 결과)와 한컴 렌더링을 비교해 **미리보기 신뢰도**를 기록한다
   (미리보기는 편집 UI용 근사 렌더링이므로 완전 일치가 목표는 아님).

## 4. 실양식 실측

### 4.1 현재 상태 — 실양식 미확보

**실제 "재난 계획서" hwpx 양식은 아직 입수하지 못했다** (2026-07-11 기준).
따라서 Phase 4에서는 python-hwpx 라이브러리에 동봉된 실물 샘플(한컴 계열 도구로 생성)과
builder 생성 문서로 **대체 실측**을 수행했다.

### 4.2 대체 실측 결과 (단위 테스트에서 측정, 2026-07-11)

측정 방법: `extract_hwpx` → 섹션별 `parse_section`(전역 id = 로컬 id + 이전 섹션 노드 수 누적)
→ `hwpx_to_html`의 `data-id` 집합과 대조 → `validate_hwpx`.

| 파일 | 섹션 | 노드 수 | body_text | table_cell (표 수) | HTML data-id 일치 | validate |
|---|---|---|---|---|---|---|
| `data/Skeleton.hwpx` (빈 골격) | 1 | 1 | 1 | 0 (0) | 일치 | 통과 |
| `conformance/corpus/report_table.hwpx` (표 보고서) | 1 | 9 | 3 | 6 (표 1개, 2×3) | 일치 | 통과 |
| `conformance/corpus/meeting_summary.hwpx` | 1 | 3 | 3 | 0 (0) | 일치 | 통과 |
| `conformance/corpus/notice.hwpx` | 1 | 4 | 4 | 0 (0) | 일치 | 통과 |
| `design/profiles/report/template.hwpx` | 1 | 1 | 1 | 0 (0) | 일치 | 통과 |
| builder 생성 데모 양식 (`tests/conftest.py`의 `DEMO_SPEC`) | 1 | 19 | 9 | 10 (표 2개: 2×2, 3×2) | 일치 | 통과 |

- 편집 왕복 실측: `report_table.hwpx`의 표 셀과 데모 양식의 문단·셀에 `apply_edits` 적용 후
  `validate_hwpx` 통과 + 재파싱으로 새 텍스트 확인 + 나머지 노드 원문 바이트 불변 확인
  (`tests/test_hwpx_edits.py`).
- placeholder 실측: 데모 양식에서 8건 탐지
  (bracket 2: `[기관명]`·`[주소 입력]`, angle 1: `<담당자 이름>`, date_stub 3: `YYYY`·`MM월`·`DD일`,
  filler 2: `○○`·`TBD`) — `tests/test_hwpx_placeholder.py`.
- 한계: 동봉 샘플은 모두 단일 섹션·수십 노드 규모다. **다중 섹션·수백 노드·셀 병합·중첩 표**가
  있는 실양식에서의 검증은 아래 4.3으로 이월한다 (파서·HTML 변환기는 중첩 표/병합 셀 로직을
  갖추고 있으나 실물 표본으로는 미검증 — 추정이 아닌 미측정 상태임을 명시).

### 4.3 실양식 입수 시 재실측 체크리스트

실제 재난 계획서 hwpx를 입수하면 아래 순서로 재실측하고 이 문서에 결과를 추가한다.
(원본은 `data/` 아래에 두되 민감 정보 포함 시 git 커밋 제외)

- [ ] **1. extract**: `extract_hwpx`로 해제 성공, 엔트리 수·섹션 파일 수 기록
- [ ] **2. validate(원본)**: `validate_hwpx` 통과 확인 — 실패 시 원본 자체 특이사항 기록
- [ ] **3. parse**: 섹션별 `parse_section` 노드 수(body_text/table_cell/표 수) 기록,
      셀 병합(`cell_col_span/row_span > 1`)·중첩 표 존재 여부 확인
- [ ] **4. to_html 불변식**: `hwpx_to_html`의 `data-id` 집합 == 전역 id 집합 확인
      (어긋나면 `RuntimeError` — 파서 순번 규칙의 미지원 구조 발견 신호이므로 해당 구조 기록)
- [ ] **5. placeholder**: `collect_placeholders` 탐지 건수·유형 기록,
      실양식 고유 표식이 `PLACEHOLDER_PATTERNS`에 안 잡히면 패턴 보강
- [ ] **6. apply_edits 왕복**: 문단 1개·표 셀 1개 이상 편집 → `validate_hwpx` 통과 →
      재파싱으로 반영·비편집 노드 불변 확인
- [ ] **7. 한컴 열람**: 3장의 육안 확인 절차 전체 수행 (원본과 편집본 모두)
- [ ] **8. 회귀 테스트 편입**: 재배포 가능한 양식이면 `tests/` 픽스처로 추가,
      불가하면 측정 수치만 이 문서에 기록
