# Handoff: 재난안전계획서 생성 도구 — UNE 디자인 시스템 UI 적용

## Overview
기존 재난안전계획서 생성 도구(CADM)의 Vue3 SPA(`web/`)에 **UNE 디자인 시스템** 기반의 새 UI를 적용하기 위한 핸드오프 패키지다.
대상 코드베이스: `report/` 저장소의 `web/src` (Vue 3 + Vite, 컴포넌트: `App.vue`, `pages/GeneratorPage.vue`, `pages/EditorPage.vue`, `components/CriteriaPanel.vue`, `ChatPanel.vue`, `TocView.vue`, `ReportView.vue`, `PreviewPanel.vue`, 전역 스타일 `style.css`).

## About the Design Files
`prototype/` 안의 파일은 **HTML로 제작된 디자인 레퍼런스(프로토타입)** 이며, 그대로 배포할 프로덕션 코드가 아니다.
할 일은 이 디자인을 **기존 Vue3 코드베이스의 패턴 그대로 재구현**하는 것이다 — 기존 컴포넌트 구조·이벤트·API 연동 로직은 유지하고, 마크업/스타일과 일부 UX 요소(단계 표시줄, 접이식 패널 등)만 이 문서 사양에 맞게 바꾼다.
`prototype/재난안전계획서 생성도구.dc.html`은 브라우저에서 직접 열어 동작(단계 전환·스트리밍 시뮬레이션·다크 모드)을 확인할 수 있다 (`prototype/` 폴더 전체를 로컬 서버로 서빙해야 함, 예: `npx serve prototype`).

## Fidelity
**High-fidelity (hifi)**. 색·타이포·간격·상태가 모두 UNE 디자인 토큰으로 확정된 값이다. 픽셀 단위로 재현하되, 값은 하드코딩하지 말고 `tokens/fig-tokens.css`의 CSS 변수를 사용한다.

## 적용 절차 (권장 순서)
1. `tokens/` 폴더(`fig-tokens.css`, `typography.css`, `fonts.css`)와 `fonts/`(Spoqa Han Sans Neo ttf 4종)를 `web/src/assets/une/`로 복사하고 `main.js`에서 import.
   - `fonts.css`의 `@font-face` 경로(`../assets/fonts/…`)를 복사 위치에 맞게 수정할 것.
2. `web/src/style.css`를 아래 "Design Tokens"·"Screens" 사양에 따라 토큰 기반으로 재작성 (클래스 구조는 유지 가능).
3. 다크 모드: `document.documentElement.dataset.theme = 'dark'` 토글만으로 전체 토큰이 전환된다 (`:root[data-theme="dark"]` 스코프가 fig-tokens.css에 포함). 테마 상태는 localStorage에 저장.
4. 신규 UX 요소 구현: 단계 표시줄(StepBar), 기준정보 패널 접기, 상태 배지(pill), 문서형 본문 뷰, 토스트.

## Screens / Views

### 0) 공통 셸 (`App.vue`)
- **상단바**: 높이 56px, 배경 `--color-surface-primary`, 하단 1px `--color-border-default`.
  - 좌: 브랜드 "une" (20px/700, `--color-text-brand`) + "재난안전계획서 생성 도구" (15px/700). 브랜드 마크 없음 — 텍스트만 사용(디자인 시스템 규칙).
  - 모드 탭: [생성 도구 | hwpx 편집]. 버튼형, 높이 56px 전체, padding 0 16px, 14px/500. 선택: 하단 2px `--color-border-brand` + 글자 `--color-text-brand`; 비선택: 투명 보더 + `--color-text-helper`.
  - 우: "다크 모드" 라벨(13px, `--color-text-helper`) + 스위치(UNE Switch 사양: 40×24 pill, 켜짐 배경 `--color-interaction-primary-bg-default`).
- 페이지 배경: `--color-bg-subtle`. 기본 글자: `--color-text-basic`, 14px, Spoqa Han Sans Neo, letter-spacing -3%.

### 1) 생성 도구 (`GeneratorPage.vue`)
- **단계 표시줄** (신규, 상단바 바로 아래): 높이 ~42px, 배경 `--color-surface-primary`, 하단 1px 구분선.
  - 4단계: 1 기준정보 입력 → 2 목차 생성·편집 → 3 본문 생성 → 4 내보내기. phase(idle/toc/generating/done)에 매핑.
  - 번호 원: 22px 원형. 현재 단계: 배경 `--color-surface-brand`+흰 글자, 라벨 13px/700 `--color-text-basic`. 지난 단계: 배경 `--color-surface-brand-subtle`+`--color-text-brand`. 미래: `--color-surface-gray-subtle`+`--color-text-muted`. 단계 사이 32px 수평선(`--color-border-default`).
  - 우측 끝 캡션: "T3Q 재난안전계획서 API (API-RPT-001 / 002)" 12px `--color-text-muted`.
- **3분할**: 기준정보 330px 고정 | 채팅 340px 고정 | 결과 flex(최소 420px). 컨테이너에 `overflow-x:auto`.

#### 기준정보 패널 (`CriteriaPanel.vue`)
- 헤더: "기준정보 입력"(14px/700) + "* 필수"(12px, `--color-text-error`) + 접기 버튼 «(24px, hover 배경 `--color-surface-gray-subtle`).
- **접힘 상태** (신규): 44px 세로 레일 — » 버튼 + 세로쓰기 라벨 "기준정보 입력".
- 필드 그룹 4종(배경정보/내용지침/표현규칙/문서 작성 목적): 1px `--color-divider-gray-light` 보더, radius 8, padding 12, 그룹 제목 12px/700 `--color-text-brand`.
- 필드 라벨: 12.5px/500 `--color-text-helper`, 필수 표시 * 는 `--color-text-error`.
- input/select/textarea: 높이 32px, padding 0 10px, 1px `--color-border-default`, radius 4, 배경 `--color-surface-primary`, 글자 13px `--color-text-basic`.
  - focus: `border-color: --color-border-brand` + `box-shadow: 0 0 0 2px --color-focus-ring`, outline none.
- 체크박스(타깃 독자): 15px, `accent-color: --color-light-blue-500`.
- "기준정보 미리보기" details: summary 13px `--color-text-brand`; pre 배경 `--color-surface-inverse-2`(#1C202A), 글자 #EBECF0, 11.5px, radius 6.
- 폼 항목·선택지는 기존 CriteriaPanel.vue와 동일 (변경 없음).

#### 채팅 패널 (`ChatPanel.vue`)
- 헤더 "대화" 14px/700. 메시지 영역 padding 14px 16px, gap 12px, 자동 하단 스크롤 유지.
- 말풍선: padding 8px 12px, radius 8, 13px, line-height 1.65. 사용자: 배경 `--color-surface-brand-subtle`. 어시스턴트: `--color-surface-gray-subtle`. 발신자 라벨 11.5px `--color-text-muted` ("나"/"어시스턴트").
- 상태 바(생성 중): 배경 `--color-surface-brand-subtle`, 글자 12.5px `--color-text-brand`, 좌측 16px 스피너(UNE Spinner). 예: "목차 생성 중… (API-RPT-001)", "본문 생성 중… (3/12)".
- 입력줄: textarea(2행, 위 input 사양과 동일) + [전송] 버튼(높이 32px, radius 8, padding 0 14px, 배경 `--color-interaction-primary-bg-default`, hover `-hover`, disabled `-disabled`+`--color-text-disabled`, 글자 `--color-text-on-brand` 13px/500).

#### 결과 영역 — 3가지 상태
1. **초기(empty)**: 중앙 정렬 카드 — 56px 원(배경 `--color-surface-brand-subtle`, "01" 15px/700 브랜드색) + "아직 생성된 문서가 없습니다"(16px/700) + 안내문 13.5px `--color-text-helper`.
2. **목차 뷰** (`TocView.vue`):
   - 툴바: "목차"(14px/700) + "— {제목}"(13px `--color-text-helper`) + [목차 재요청](outline·grayscale 버튼) + [본문 생성](fill·primary 버튼). 버튼 높이 32px(UNE Button size xs).
   - 도움말 줄 12.5px `--color-text-muted`.
   - 트리(2단계): 장(챕터) 행 — 인라인 input 14px/700, 투명 보더(hover/focus 시 보더·배경 표시); 하위 행 — 들여쓰기 28px, 13.5px. 행 hover 배경 `--color-surface-gray-subtler`.
   - 행 액션(▲ ▼ + ×): 24px 정사각 ghost 버튼, hover `--color-surface-gray-subtle`; 삭제 ×는 `--color-text-error`, hover `--color-surface-error-subtle`. 하위 추가 +는 챕터 행에만.
   - 하단 "+ 최상위 목차 추가" 링크 버튼(13px `--color-text-brand`).
3. **본문(보고서) 뷰** (`ReportView.vue`) — 문서형으로 리디자인:
   - 툴바: 제목(14px/700) + 진행 pill + 서식 select + [목차로](ghost) + [hwpx 내보내기](fill·primary) + [docx 내보내기](outline·primary).
   - 진행 pill: 12px, padding 2px 10px, radius 1000. 생성 중: `--color-surface-brand-subtle`/`--color-text-brand` "생성 중 3/12"; 완료: `--color-surface-success-subtle`/#1D792B "12/12 완료".
   - 본문: 회색 캔버스 위 **문서 카드** — max-width 800px 중앙, 배경 `--color-surface-primary`, radius 8, 그림자 `0 0 5px rgba(0,0,0,.06), 0 8px 15px rgba(0,0,0,.15)`, padding 56px 64px 72px.
   - 문서 제목 24px/700 중앙 + 부제 "서면 보고 / {보고일시} / {역할}" 13px `--color-text-helper`.
   - 장 제목: 17px/700, 하단 2px `--color-border-brand` 보더.
   - 목차(리프) 섹션: 제목 14.5px/700 + 상태 pill(11.5px, radius 1000):
     대기 `--color-surface-gray-subtle`/`--color-text-helper` (섹션 전체 opacity 0.45) · 생성 중 `--color-surface-brand-subtle`/`--color-text-brand` + 14px 스피너 · 완료 `--color-surface-success-subtle`/#1D792B · 오류 `--color-surface-error-subtle`/`--color-text-error`.
   - 본문 줄: 13.5px, line-height 1.75. 개조식 들여쓰기 — `□` 0 / `○` 16px / `―` 30px.
   - 참조문서: "참조 · {파일명 (p.N)}" 12px `--color-text-muted`.

### 2) hwpx 편집 (`EditorPage.vue` + `PreviewPanel.vue`)
- 2분할: 채팅 360px | 미리보기 flex(최소 420px).
- 미리보기 툴바: "미리보기"(14px/700) + 파일 메타(12.5px `--color-text-helper`, 예: "사업계획서(신청용).hwpx · 2페이지 · 원본 서식 보존") + 선택 pill(12px, `--color-surface-warning-subtle`/`--color-surface-warning`, "선택: {요소}") + [hwpx 업로드](outline·primary) + [hwpx 다운로드](fill·primary, 업로드 전 disabled).
- 업로드 전: 대시 보더(1.5px dashed `--color-border-default`) 카드, radius 12, hover 시 보더 `--color-border-brand`+배경 `--color-surface-brand-subtle`.
- 문서 미리보기: 본문 뷰와 동일한 문서 카드. 표: 라벨 셀 120px 배경 `--color-surface-gray-subtle`, 상단 2px 진한 보더.
- **요소 선택**: 문단/셀 클릭 → `outline: 2px solid --color-surface-warning` (offset -2). 재클릭 해제.
- **변경 표시**: 편집된 요소 글자 `--color-text-error`(빨강) + 노란 하이라이트 페이드아웃 2.4s (`highlight-fade`: `--color-yellow-100` → transparent). 다음 편집 시 이전 changed 해제.

## Interactions & Behavior
- 모드 전환: 탭 클릭. 각 페이지 상태 보존(기존 v-show 방식 유지).
- 생성 흐름: 채팅에서 트리거 키워드(`/(작성|생성|만들|초안|목차)/`) → 필수값 검증(미입력 라벨 안내) → 목차 생성(RPT-001, 로딩 상태바) → 목차 편집 → [본문 생성] → SSE로 목차별 순차 수신(대기→생성 중→완료, 진행 카운터 갱신) → 내보내기.
- 내보내기 성공: **토스트** 하단 중앙 — 배경 `--color-surface-inverse-2`, 흰 글자 13.5px, radius 8, 그림자 `0 8px 15px rgba(0,0,0,.32)`, 좌측 8px 녹색 점, 3.2초 후 자동 숨김.
- 애니메이션은 최소(스피너, 하이라이트 페이드, 토스트 등장 정도) — UNE 규칙상 과한 모션 금지.
- 반응형: 3분할 행에 `overflow-x:auto`, 결과/미리보기 최소 420px.

## State Management
기존 GeneratorPage/EditorPage의 상태 그대로 사용. 추가 상태: `theme`('light'|'dark', localStorage 저장), `criteriaOpen`(boolean), 토스트 메시지. phase(idle|toc|generating|done)는 단계 표시줄과 결과 뷰 분기에 재사용.

## Design Tokens
전체 509개 변수는 `tokens/fig-tokens.css` 참조. 핵심(라이트 기준 실값):
- Primary #3C69FC (`--color-interaction-primary-bg-default`), hover #345CE0, active #2C4EC4
- 텍스트: basic #444A57계열(grayscale-800) / helper / muted / brand #3C69FC / error #D92D20 / disabled
- 표면: primary #FFF / subtle(페이지 배경) / gray-subtle / brand-subtle / success-subtle / error-subtle / warning-subtle / inverse-2 #1C202A
- 보더: default #CECFD2, subtle(divider-gray-light) #EBECF0, brand #3C69FC; focus ring `--color-focus-ring`
- Radius: 4(입력) / 8(버튼·카드·말풍선) / 12(업로드 카드) / 1000(pill)
- 타이포: Spoqa Han Sans Neo, letter-spacing -3%; 본문 13–14px, 제목 14–24px
- 그림자: `0 0 5px rgba(0,0,0,.06), 0 8px 15px rgba(0,0,0,.15)` (다크 모드 .08/.32)
- 다크 모드: `:root[data-theme="dark"]`로 자동 전환 — 색상 하드코딩 금지, 반드시 var() 사용

## Assets
- `fonts/` — Spoqa Han Sans Neo Light/Regular/Medium/Bold (자체 호스팅, 라이선스: SIL OFL)
- `tokens/` — fig-tokens.css(전체 변수, 다크/고대비/모바일 스코프 포함), typography.css, fonts.css
- 아이콘: 이모지·유니코드 문자 아이콘 금지. 필요 시 UNE 아이콘 세트(24×24, currentColor) 사용 — 원본은 디자인 시스템 저장소 `assets/icons/icon-data.js`.

## Files
- `prototype/재난안전계획서 생성도구.dc.html` — 동작하는 hifi 프로토타입 (전체 흐름·다크 모드 확인용; 폴더째 로컬 서빙 필요)
- `prototype/support.js`, `prototype/_ds/` — 프로토타입 런타임·디자인 시스템 번들 (참고용, 이식 대상 아님)
- `tokens/`, `fonts/` — **실제로 코드베이스에 복사할 파일**
