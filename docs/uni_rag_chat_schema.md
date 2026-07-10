# UNI RAG `/chat/` 응답 스키마 실측 결과 (Phase 5 T1)

- 실측일: 2026-07-11
- 서버: `http://221.147.100.161:8000`, 모델: `qwen3-coder-next`
- 도구: `scripts/probe_uni_rag.py` (원시 출력 전체: `scripts/probe_output.txt`)
- 요청 바디: `docs/analysis.md` §4.1 그대로 — `{query, model_key, history, stream, top_k: 5, thinking: false}`
- 인증: `POST /auth/login` `{account, password}` → 응답의 `access_token`(JWT, 실측 길이 500자) → `Authorization: Bearer`

## 1. stream=false — JSON 응답 구조

`HTTP 200`, `content-type: application/json`. 최상위 키는 정확히 2개다.

```json
{
  "answer": "...(답변 텍스트 전체)...",
  "sources": [
    { "filename": "5384_지방세징수법 시행규칙.xml", "score": 0.5895425, "text": "...(청크 발췌, 말줄임)..." }
  ]
}
```

| 필드 경로 | 타입 | 설명 |
|---|---|---|
| `answer` | string | **답변 텍스트 필드. chat 래퍼가 사용할 유일한 경로.** |
| `sources` | array | RAG 검색 근거 청크. 항상 `top_k`개(=5) 첨부됨 |
| `sources[].filename` | string | 원본 문서 파일명 (.xml/.hwp 등) |
| `sources[].score` | float | 리랭킹 점수 (0~1) |
| `sources[].text` | string | 청크 발췌 (약 200자 + `...` 절단) |

주의점 (chat 래퍼 T3 함의):

- **세션/토큰 사용량 필드 없음.** `session_id` 미전송 시 응답에도 세션 관련 필드가 없고, usage(토큰 카운트)류 필드도 없다. 히스토리는 클라이언트가 `history` 배열로 직접 관리해야 한다(설계대로).
- **`sources`는 질의 관련성과 무관하게 항상 붙는다.** 자기소개 요청에도 법령 XML 5건이 첨부됨(내부 RAG 검색이 무조건 수행). 문서 편집 용도에서는 `sources`를 무시하고 `answer`만 취하면 된다. RAG 검색 비용을 줄이는 옵션(top_k=0 등)이 동작하는지는 미확인(추측: 서버가 항상 검색 수행).

## 2. stream=true — SSE 이벤트 포맷

`HTTP 200`, `content-type: text/event-stream; charset=utf-8`. 이벤트는 `data:` 라인 + 빈 라인 쌍으로 온다 (`event:`/`id:` 필드 없음). 페이로드는 3종:

```
data: "1"                          ← ① 토큰 델타: JSON 문자열 리터럴 (json.loads 필요)
data: ","
data: " "
...
data: {"__sources__": [ ... ]}     ← ② 완료 직전 1회: sources 배열을 담은 JSON 객체
data: [DONE]                       ← ③ 종료 신호: 리터럴 문자열 (JSON 아님)
```

파서 규칙 (실측 기반):

1. `data: ` 접두어 제거 후 페이로드 판별.
2. 페이로드가 `[DONE]` → 스트림 종료.
3. `json.loads` 성공 & 결과가 **문자열** → 답변 토큰 델타, 순서대로 이어붙인다. 공백·구두점도 개별 이벤트로 오므로 델타를 그대로 연결(구분자 삽입 금지).
4. `json.loads` 성공 & 결과가 **dict이며 `__sources__` 키 보유** → RAG 근거(비스트림의 `sources`와 동일 구조). 답변 텍스트에 포함하지 않는다.
5. 토큰 델타가 JSON 문자열 리터럴이므로 유니코드 이스케이프·따옴표는 `json.loads`가 처리한다. 단순 따옴표 벗기기로 구현하지 말 것.

실측 예 (질의 "1부터 5까지 숫자를 세어주세요", 총 30라인): 델타 13개(`"1"`,`","`,`" "`,...,`"5"`) → `__sources__` 1개 → `[DONE]`.

## 3. 품질 실측 샘플 (qwen3-coder-next 1차 평가)

### 샘플 ① 한국어 공문서 어투 교정 (stream=false)

- 요청: `아래 문장을 공문서 어투로 고쳐줘: 태풍 오면 주민들 빨리 대피시키고 물자도 좀 챙겨놔야 함.`
- 응답 `answer`:

  > 태풍 발생 시 주민의 안전 확보를 위해 신속한 대피를 실시하고, 긴급 구호 물자도 사전에 확보·비축하여 재난 대응에 만전을 기해야 합니다.

- 평가: 구어체 → 공문서체 변환이 자연스럽고 어휘 선택(실시·확보·비축·만전)이 적절. 부가 설명 없이 교정문만 반환. 코딩 특화 모델임에도 한국어 공문서 어투 품질은 **1차 합격**.

### 샘플 ② edits 배열 JSON 지시 준수 (stream=false)

- 요청: `다음 지시를 edits 배열 JSON으로만 답하라: 문단 3의 텍스트를 '2026년 상반기 보고'로 바꿔라. 형식: {"edits":[{"id":3,"new_text":"..."}]}`
- 응답 `answer` (원문 그대로):

  ```json
  {"edits":[{"id":3,"new_text":"2026년 상반기 보고"}]}
  ```

- 평가: **완전 준수.** 마크다운 코드펜스·서두 문장·후행 설명 없이 순수 JSON만 반환. `id` 타입(숫자)·필드명도 형식 예시와 일치. 단, 단일 샘플이므로 T3의 JSON 복구 파서(코드펜스 제거·부분 파싱)는 안전망으로 유지한다.

### 1차 종합

한국어 지시 이해·공문서 어투·JSON 스키마 준수 모두 양호. analysis.md §4.3에서 우려한 "코딩 특화 모델의 한국어 품질" 리스크는 이번 실측 범위에서는 관찰되지 않았다(문서 편집 시나리오의 긴 문단·표 편집 품질은 Phase 5 T4 이후 실험에서 추가 검증 필요).

## 4. 세션·토큰 관련 필드

- `/chat/` 응답(비스트림·스트림 모두)에 세션 ID, 토큰 사용량, 모델명 에코 등의 메타 필드 **없음**. 순수 `answer`+`sources`(스트림은 델타+`__sources__`+`[DONE]`)뿐이다.
- 로그인 응답의 토큰 키는 `access_token` (기존 `UniRagClient.login()`이 이미 처리). 만료 시간 필드는 이번 프로브에서 별도 확인하지 않음 — 설계대로 401 시 재로그인 정책 유지.
