"""T3Q 실서버 스모크 프로브 (수동 실행 — CI 아님).

사용:
  .venv/Scripts/python scripts/probe_t3q.py toc       # 목차 생성 (API-RPT-001)
  .venv/Scripts/python scripts/probe_t3q.py content   # 본문 스트리밍 (API-RPT-002)
  .venv/Scripts/python scripts/probe_t3q.py llm       # 편집 모드 LLM (API-LLM-001)

.env의 T3Q_* 설정을 그대로 쓴다 (TLS 우회 설정은 docs/t3q_upgrade_design.md §6).
"""
import asyncio
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")

from app.llm.t3q_client import T3qChatBackend, T3qReportClient  # noqa: E402

CRITERIA = {
    "subject": "코로나19 재유행 대비계획",
    "backgroundInfo": {"disasterType": "감염병", "controlPhase": "대비"},
    "contentInstruction": {
        "writingGuide": "1., 1.1. 형식으로 2단계 계층까지, 각 계층 최대 2개 섹션",
    },
    "purposeOfDocument": {
        "goalOfBusiness": "재난안전계획서 작성",
        "role": "재난안전계획 수립 담당자",
        "targetAudiences": ["중앙정부", "지자체"],
    },
}


async def probe_toc() -> dict:
    result = await T3qReportClient().generate_toc(CRITERIA)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    return result


async def probe_content() -> None:
    toc = await probe_toc()
    print("\n--- 본문 스트리밍 시작 ---")
    async for item in T3qReportClient().generate_content(CRITERIA, toc["sections"]):
        if "error" in item:
            print(f"[오류] {item['name']}: {item['error']}")
        else:
            print(f"[수신] {item['name']} ({len(item['content'])}자, "
                  f"참조 {len(item['references'])}건)")
    print("--- 완료 ---")


async def probe_llm() -> None:
    backend = T3qChatBackend()
    answer = await backend.chat("태풍 대비 행동요령을 두 문장으로 알려줘")
    print(answer)
    print(f"\nfinish_reason: {backend.last_finish_reason}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "toc"
    runner = {"toc": probe_toc, "content": probe_content, "llm": probe_llm}.get(mode)
    if runner is None:
        raise SystemExit(f"알 수 없는 모드: {mode} (toc|content|llm)")
    asyncio.run(runner())
