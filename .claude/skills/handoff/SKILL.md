---
name: handoff
description: "세션을 마치며 다음 자리(회사↔집)로 인계할 때 사용. PROGRESS.md를 갱신하고 커밋한다."
disable-model-invocation: true
---

# 핸드오프 (세션 종료 인계)

사용자가 `/handoff`를 실행하면 다음을 수행한다.

## 1) PROGRESS.md 갱신

이번 세션에서 실제로 한 일을 바탕으로 `PROGRESS.md`를 아래 섹션 구조로 갱신한다:

- **Last updated**: 오늘 날짜 (절대 날짜)
- **Current goal**: 프로젝트 전체 목표 (변동 시에만 수정)
- **Done this session**: 이번 세션에서 완료한 것 (구체적 파일/Phase 단위)
- **In progress**: 하다가 만 것과 현재 상태
- **Next steps**: 다음 세션에서 바로 시작할 일 (구체적으로, 명령어 포함 가능)
- **Blockers**: 막힌 것, 사용자 결정/제공이 필요한 것
- **How to run**: 빌드·실행·테스트 방법 (변동 시에만 수정)

## 2) 커밋

```
git add -A && git commit -m "<이번 세션 요약 커밋 메시지>"
```

커밋 메시지는 이번 세션의 작업을 요약해 작성한다.

## 3) push 안내 (자동 push 금지)

커밋 후 사용자에게 안내만 한다: "다른 자리에서 이어가려면 `git push` 후, 그쪽에서 `git pull` → `claude` 실행하세요."
