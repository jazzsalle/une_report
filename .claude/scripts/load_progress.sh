#!/usr/bin/env bash
# SessionStart hook: PROGRESS.md + git 상태를 additionalContext로 주입한다.
# 내용은 명령문이 아닌 사실 진술로 구성한다.

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || cd "$(dirname "$0")/../.." || exit 0

CONTEXT=""

if [ -f "PROGRESS.md" ]; then
  CONTEXT="다음은 이 프로젝트의 인계 문서(PROGRESS.md) 내용이다:

$(cat PROGRESS.md)
"
else
  CONTEXT="이 프로젝트에는 아직 PROGRESS.md가 없다.
"
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  BRANCH=$(git branch --show-current 2>/dev/null)
  RECENT=$(git log --oneline -5 2>/dev/null)
  DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  CONTEXT="$CONTEXT
현재 git 상태(사실 진술):
- 브랜치: ${BRANCH:-(없음)}
- 최근 커밋 5개:
${RECENT:-(커밋 없음)}
- 미커밋 변경 파일 수: $DIRTY
"
fi

# 10,000자 제한 대비 9,000자로 절단(UTF-8 안전) + JSON 직렬화는 python에 위임.
# 멀티라인 python -c는 Windows(pyenv-win shim)에서 깨지므로 별도 .py 파일로 실행한다.
SERIALIZER="$(dirname "$0")/progress_to_json.py"
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$CONTEXT" | python3 "$SERIALIZER"
elif command -v python >/dev/null 2>&1; then
  printf '%s' "$CONTEXT" | python "$SERIALIZER"
else
  # python이 없으면 plain stdout 폴백 (stdout도 컨텍스트로 주입됨)
  printf '%s' "$CONTEXT"
fi
