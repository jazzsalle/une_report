---
name: resume-work
description: "사용자가 '이어서', '계속', 'resume', '어디까지 했지' 등으로 이전 작업을 이어가려 할 때 호출. PROGRESS.md와 git 상태를 확인하고 Next steps부터 재개한다."
---

# 작업 재개 (SessionStart hook이 비었을 때의 수동 폴백)

## 현재 인계 문서

!`cat PROGRESS.md`

## 현재 git 상태

!`git branch --show-current && git log --oneline -5 && git status --porcelain`

## 지침

1. 위 PROGRESS.md의 **Next steps** 항목을 최우선 근거로 삼아 이어서 작업한다.
2. `In progress`에 미완 작업이 있으면 그것부터 마무리한다.
3. git 상태(미커밋 변경)와 PROGRESS.md 내용이 어긋나면, 실제 파일 상태를 확인하고 사용자에게 차이를 알린 뒤 진행한다.
4. 다음 Phase 실행이 Next steps라면 `/phase-run N`으로 진행한다.
