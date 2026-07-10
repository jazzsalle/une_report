"""UNI RAG `/chat/` 응답 스키마 실측 프로브 (Phase 5 T1).

미문서화된 `/chat/` 응답 바디를 실호출로 확인한다:
  1. stream=false 1회 — 원시 JSON 전체 출력
  2. stream=true 1회 — SSE 원시 라인 전체 출력
  3. 품질 실측 ① 한국어 공문서 어투 교정 지시 (stream=false)
  4. 품질 실측 ② edits 배열 JSON 출력 지시 (stream=false)

실행 (저장소 루트에서):
    .venv/Scripts/python scripts/probe_uni_rag.py

계정은 .env의 TEST_UNE_ACCOUNT / TEST_UNE_PASSWORD를 사용한다.
결과는 stdout과 scripts/probe_output.txt(utf-8)에 동시 기록한다.
"""
import asyncio
import json
import sys
from pathlib import Path

# 저장소 루트를 모듈 경로에 추가 (scripts/ 아래에서 app 패키지 임포트용)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import config  # noqa: E402
from app.llm.uni_rag_client import UniRagClient  # noqa: E402

OUTPUT_PATH = ROOT / "scripts" / "probe_output.txt"

_out_lines: list[str] = []


def emit(text: str = "") -> None:
    """stdout(가능한 범위)과 출력 파일 버퍼에 동시 기록."""
    _out_lines.append(text)
    try:
        print(text)
    except UnicodeEncodeError:
        # Windows 콘솔(cp949)이 못 찍는 문자는 대체 출력 — 파일에는 원문이 남는다
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8"))


def chat_body(query: str, stream: bool) -> dict:
    """docs/analysis.md §4.1의 요청 바디 그대로."""
    return {
        "query": query,
        "model_key": config.UNI_RAG_MODEL_KEY,
        "history": [],
        "stream": stream,
        "top_k": 5,
        "thinking": False,
    }


async def probe_nonstream(client: httpx.AsyncClient, token: str, label: str, query: str) -> None:
    emit(f"\n{'=' * 70}\n[{label}] POST /chat/ stream=false\nquery: {query}\n{'-' * 70}")
    r = await client.post(
        f"{config.UNI_RAG_BASE_URL}/chat/",
        json=chat_body(query, stream=False),
        headers={"Authorization": f"Bearer {token}"},
    )
    emit(f"HTTP {r.status_code}  content-type: {r.headers.get('content-type')}")
    try:
        data = r.json()
        emit("-- 원시 JSON 전체 --")
        emit(json.dumps(data, ensure_ascii=False, indent=2))
        if isinstance(data, dict):
            emit(f"-- 최상위 키: {list(data.keys())}")
    except ValueError:
        emit("-- JSON 파싱 실패, 원시 텍스트 --")
        emit(r.text)


async def probe_stream(client: httpx.AsyncClient, token: str, label: str, query: str) -> None:
    emit(f"\n{'=' * 70}\n[{label}] POST /chat/ stream=true (SSE 원시 라인)\nquery: {query}\n{'-' * 70}")
    async with client.stream(
        "POST",
        f"{config.UNI_RAG_BASE_URL}/chat/",
        json=chat_body(query, stream=True),
        headers={"Authorization": f"Bearer {token}"},
    ) as r:
        emit(f"HTTP {r.status_code}  content-type: {r.headers.get('content-type')}")
        n = 0
        async for line in r.aiter_lines():
            emit(f"L{n:04d}| {line}")
            n += 1
        emit(f"-- 총 {n} 라인 수신, 스트림 종료 --")


async def main() -> int:
    account = config.TEST_UNE_ACCOUNT
    password = config.TEST_UNE_PASSWORD
    if not account or not password:
        emit("오류: .env에 TEST_UNE_ACCOUNT / TEST_UNE_PASSWORD가 설정되어 있지 않다.")
        return 1

    emit(f"UNI RAG 프로브 시작 — base_url={config.UNI_RAG_BASE_URL}, model_key={config.UNI_RAG_MODEL_KEY}")

    # 1) 로그인 (기존 UniRagClient.login 재사용)
    token = await UniRagClient().login(account, password)
    emit(f"로그인 성공 — JWT 길이 {len(token)}, 앞 12자: {token[:12]}...")

    async with httpx.AsyncClient(timeout=config.UNI_RAG_TIMEOUT) as client:
        # 2) stream=false 기본 스키마 확인
        await probe_nonstream(client, token, "PROBE-1 기본(비스트림)",
                              "안녕하세요. 한 문장으로 자기소개를 해주세요.")

        # 3) stream=true SSE 포맷 확인
        await probe_stream(client, token, "PROBE-2 기본(스트림)",
                           "1부터 5까지 숫자를 세어주세요.")

        # 4) 품질 실측 ① 한국어 공문서 어투 교정
        await probe_nonstream(
            client, token, "PROBE-3 품질(한국어 공문서 어투)",
            "아래 문장을 공문서 어투로 고쳐줘: 태풍 오면 주민들 빨리 대피시키고 물자도 좀 챙겨놔야 함.",
        )

        # 5) 품질 실측 ② edits 배열 JSON 지시 준수
        await probe_nonstream(
            client, token, "PROBE-4 품질(JSON 지시 준수)",
            '다음 지시를 edits 배열 JSON으로만 답하라: 문단 3의 텍스트를 '
            "'2026년 상반기 보고'로 바꿔라. "
            '형식: {"edits":[{"id":3,"new_text":"..."}]}',
        )

    emit(f"\n프로브 완료 — 전체 출력을 {OUTPUT_PATH}에 저장한다.")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        OUTPUT_PATH.write_text("\n".join(_out_lines) + "\n", encoding="utf-8")
    sys.exit(code)
