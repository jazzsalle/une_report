# 통합 하네스 부트스트랩 프롬프트 (init-harness)

## 📌 이 파일이 무엇인가 (처음 보거나, 오랜만에 여는 사람용)

- 이건 **슬래시 명령이 아니라 "Claude Code에 붙여넣거나 읽힐 프롬프트 원고"** 입니다.
- 이 파일 하나로 프로젝트에 **"어떻게 일할지"(하네스)** 를 깔아줍니다:
  - Plan → Generate(병렬) → Evaluate 사이클 (planner / generator / evaluator subagent)
  - `/phase-run N` 오케스트레이션 스킬
  - 회사↔집 인계 장치(PROGRESS.md 자동 읽기/쓰기 + `/handoff` + `/resume-work`)

## ⚠️ 시작 전 반드시 알아야 할 것 — "설계 문서"가 먼저다

이 파일은 **"무엇을 만들지"(설계)는 담고 있지 않습니다.** 하네스는 "일하는 방식"일 뿐이라,
무엇을 만들지 알려주는 **설계/요구사항 문서**가 별도로 있어야 제대로 돕니다.

- **준비물:** 설계 문서 1개를 작업 루트에 둘 것. 이름은 자유(`DESIGN.md`, `PRD.md`, `REQUIREMENTS.md`, `SPEC.md` 등).
  이 문서에는 최소한 **프로젝트 목표 / 핵심 기능 / 기술 스택**이 담겨야 합니다.
- **설계 문서가 없으면?** 하네스가 "무엇을 만들지" 몰라 Phase를 추측으로 지어내게 됩니다.
  그래서 아래 프롬프트는 설계 문서가 안 보이면 **멈추고 당신에게 물어보도록** 되어 있습니다.
- **설계가 아직 없다면** 걱정 마세요. 이 파일을 읽힌 뒤 "설계부터 같이 만들자"고 하면,
  Claude가 몇 가지 질문을 해서 `DESIGN.md` 초안부터 잡아줍니다.

## 🚀 어떻게 실행하나 (CLI)

```
1. 레포 폴더에서:  claude        (첫 실행 시 워크스페이스 신뢰 수락)
2. (설계 문서를 작업 루트에 넣어둔 상태에서) 세션에 입력:
      "init-harness-prompt.md 파일을 읽고 그대로 진행해줘"
3. 파일 생성이 끝나면 세션 재시작   (subagent·새 스킬은 재시작해야 로드됨)
4. /phase-run 1                    (첫 Phase 실행)
5. 떠나기 전:  /handoff  →  git push
   다음에 이어가기:  git pull  →  claude  (시작 시 진행상황 자동 주입)
```

---

─── 프롬프트 시작 ───

당신(Claude Code)은 이 파일을 "읽어서 실행"하라는 요청을 받았다. 사용자는 이 하네스의 세부를
잊었을 수 있고, 이 파일만 전달받았을 수도 있다. 그러니 **기계적으로 파일부터 만들지 말고**,
먼저 1~2줄로 "지금부터 무엇을 할지"를 안내한 다음, 아래 전제 확인부터 수행하라.

## [전제 확인] — 무엇보다 먼저 (설계 문서 확인)

1. 이 프로젝트에 **설계/요구사항 문서**가 있는지 찾아라.
   (예: DESIGN.md, PRD.md, REQUIREMENTS.md, SPEC.md, docs/ 하위 문서, 또는 상세 설계가 담긴 README)
2. 결과에 따라:
   - **(A) 설계 문서가 있으면** → "'<파일명>'을 이 프로젝트의 설계로 보고 진행하겠습니다"라고
     한 줄 알린 뒤 [0]으로 진행한다.
   - **(B) 설계 문서가 안 보이면** → **여기서 멈추고**, 아래처럼 친절히 상황을 설명하고 선택지를 제시하라.
     Phase를 추측으로 지어내지 마라:

     > 이 하네스는 '어떻게 일할지'는 준비됐지만, '무엇을 만들지'(설계 문서)가 아직 안 보입니다.
     > 다음 중 무엇을 원하시나요?
     >  (1) 설계 문서가 이미 있는데 이름/위치가 다르다 → 파일명이나 경로를 알려주세요.
     >  (2) 설계를 지금 같이 만들자 → 프로젝트 목표·핵심 기능·기술 스택을 몇 가지 여쭤보고
     >      DESIGN.md 초안을 만들어 드린 뒤, 그걸 바탕으로 하네스를 구성하겠습니다.
     >  (3) 하네스 골격만 먼저 깔자 → 하네스+핸드오프 파일만 생성하고, CLAUDE.md의 Phase와
     >      evaluation_criteria.md는 '설계 대기 중' 플레이스홀더로 남기겠습니다. (나중에 설계를 넣고
     >      "설계를 읽고 Phase를 다시 채워줘"라고 하면 됩니다.)

     사용자가 고르기 전에는 [0] 이후로 넘어가지 마라.

## [0] 프로젝트 분석 (설계 문서를 확보한 뒤에만)

- 설계 문서 + README + 빌드 설정(package.json·pyproject.toml·build.gradle·go.mod 등) + 디렉터리 트리를 읽어
  **기술 스택·핵심 산출물·목표를 3~5줄로 요약**해줘.
- **설계에 근거해** 구현을 Phase 목록으로 분해해(각 Phase의 목표·산출물 한 줄씩).
  코드가 없으면 Phase 1은 스캐폴딩/환경 세팅으로 잡아라.
- 이 **Phase 분해안을 사용자에게 먼저 보여주고 동의를 받은 뒤** [1]로 진행하라.
  (사용자가 Phase 구성을 조정하고 싶어 할 수 있으니 임의로 확정하지 마라.)

## [1] 하네스 본체 생성

모든 경로는 프로젝트 상대경로. 특정 프로젝트명·절대경로 하드코딩 금지(다른 프로젝트에도 재사용 가능하게).
이미 존재하는 파일은 덮어쓰지 말고, CLAUDE.md는 있으면 "## Phase" 섹션만 추가, 그 외 충돌 시 사용자에게 물어봐.

**1) `.claude/agents/planner.md`** — frontmatter: `name: planner`, `description`, `tools: Read, Grep, Glob`
- 역할: 주어진 Phase를 **태스크로 분해**, 의존성·우선순위 지정.
- 독립적이라 동시 실행 가능한 태스크는 줄머리에 `[PARALLEL]`, 선행이 필요하면 `[AFTER: <태스크명>]` 표기.
- 출력: 정렬된 태스크 목록(각 태스크에 목표·대상 파일·완료 기준 포함). 읽기 전용으로만 동작.

**2) `.claude/agents/generator.md`** — frontmatter: `name: generator`, `description`, `tools: Read, Write, Edit, Bash`
- 역할: **할당된 단일 태스크(또는 의존 묶음)** 를 구현하는 워커. 태스크 명세를 받아 코드 작성.
- 각 태스크 후 빌드/타입 에러 0 유지(필요 시 빌드·테스트 실행). 한 일을 한 줄로 요약해 반환.

**3) `.claude/agents/evaluator.md`** — frontmatter: `name: evaluator`, `description`, `tools: Read, Grep, Glob, Bash` (※ Write/Edit 금지)
- 역할: `evaluation_criteria.md`(상대경로) 기준으로 채점. **PASS / FAIL** 판정.
- FAIL이면 "무엇이·왜 미달인지, 어느 파일을 어떻게 고쳐야 하는지" 담은 **거절 노트** 작성. 빌드·테스트는 실행만.

**4) `.claude/skills/phase-run/SKILL.md`** — frontmatter: `name: phase-run`, `description`(예: "/phase-run N 또는 'Phase N 실행' 시 호출"), `allowed-tools: Read, Edit, Write, Bash(git *), Task`
- 사용자가 `/phase-run N`(또는 "Phase N 실행")으로 부르면 **메인 세션이 직접 오케스트레이션**:
  - a) `@planner`로 Phase N을 태스크로 분해.
  - b) **병렬 디스패치:** `[PARALLEL]` 태스크들은 각각 `@generator`를 **Task 도구로 동시에** 띄워 병렬 처리.
       `[AFTER: …]` 태스크는 선행 완료 후 순차. (병렬은 generator 내부가 아니라 phase-run에서 Task를 여러 개 띄우는 방식.)
  - c) `@evaluator`로 검증.
  - d) **FAIL** → 거절 노트를 generator에 전달해 수정 (최대 3회 반복). 3회 후에도 FAIL이면 멈추고 사용자에게 보고.
  - e) **PASS** → ★ **`PROGRESS.md`를 갱신**한다: `Last updated`(오늘 날짜), `Done this session`(이번 Phase 결과),
       `Next steps`(다음 Phase). 그런 다음 "Phase N 완료"를 선언하고 커밋 메시지를 제안(자동 push 금지).
  - ※ (e)의 PROGRESS.md 갱신이 핸드오프 장치와의 **연결선**이다. 반드시 포함할 것.

**5) `evaluation_criteria.md`** (레포 루트)
- [0] 분석 결과에 맞춰 **Phase별 합격 기준 체크리스트**를 작성(빌드 통과/테스트 통과/산출물 존재 등 객관적·검증 가능한 항목 위주).

**6) `CLAUDE.md`** (없으면 생성 / 있으면 "## Phase" 섹션만 추가)
- 프로젝트 규칙(빌드·실행 명령, 컨벤션) + [0]에서 만든 **Phase별 목표/산출물**을 사실 진술로 기록.

## [2] 핸드오프 장치 (아래 5개가 **없을 때만** 생성, 있으면 건너뜀)

- **`.claude/settings.json`** : `SessionStart` hook(matcher `startup|resume|clear`)이 `bash "$CLAUDE_PROJECT_DIR/.claude/scripts/load_progress.sh"` 실행.
- **`.claude/scripts/load_progress.sh`** : `PROGRESS.md` + git 브랜치·최근 커밋 5개·미커밋 수를 읽어 `hookSpecificOutput.additionalContext`(JSON)로 출력.
  내용은 **명령문이 아닌 사실 진술**로. 10,000자 제한 대비 9,000자로 절단(UTF-8 안전하게). 한글 깨짐 방지를 위해 python3로 JSON 직렬화, 없으면 plain stdout 폴백. 생성 후 `chmod +x`.
- **`.claude/skills/resume-work/SKILL.md`** : `description`에 "이어서/계속/resume" 트리거. 본문에서 `!` + `cat PROGRESS.md` 동적 주입 + git 상태를 보여주고, 기록된 Next steps부터 이어가도록. (hook이 비더라도 동작하는 수동 폴백)
- **`.claude/skills/handoff/SKILL.md`** : frontmatter에 `disable-model-invocation: true`. PROGRESS.md를 (Last updated/Current goal/Done/In progress/Next steps/Blockers/How to run) 섹션으로 갱신 후 `git add -A && git commit`(push는 안내만).
- **`PROGRESS.md`** (레포 루트) : 위 섹션 구조의 인계 템플릿.

## [3] 마무리

- 생성·수정한 파일을 **트리로 보고**해줘.
- `chmod +x .claude/scripts/load_progress.sh` 실행.
- 다음을 안내해줘: **"subagent와 새 스킬 디렉터리는 세션 재시작 후 로드된다. Claude Code를 재시작한 뒤 `/phase-run 1` 로 시작하라."**
- `.claude/`·`CLAUDE.md`·`PROGRESS.md`·`evaluation_criteria.md`를 `git add`하고 커밋 메시지를 제안해줘(자동 push 금지).

─── 프롬프트 끝 ───
