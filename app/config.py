"""환경 설정. .env 파일과 환경변수에서 읽는다."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# UNI RAG System
UNI_RAG_BASE_URL = os.getenv("UNI_RAG_BASE_URL", "http://221.147.100.161:8000")
UNI_RAG_MODEL_KEY = os.getenv("UNI_RAG_MODEL_KEY", "qwen3-coder-next")
UNI_RAG_TIMEOUT = float(os.getenv("UNI_RAG_TIMEOUT", "120"))
# /chat/ 요청에 동봉하는 생성 길이 상한. 서버(vLLM) 기본값(512~1024 추정)에
# 응답이 잘리는 문제 대응. 0이면 필드를 보내지 않는다(서버 기본값 사용).
# 서버가 필드를 무시할 수 있음 — passthrough 여부는 담당자 협의 대상.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
# edit·query 프롬프트에 싣는 노드 목록의 문자 수 예산. UNI RAG는 쿼리가
# 약 48k자를 넘으면 HTTP 500을 반환한다(2026-07-13 실측: 48k OK / 54k 500).
# RAG 청크·서버 프롬프트·대화 이력 여유분을 감안해 30k로 잡는다.
PROMPT_CHAR_BUDGET = int(os.getenv("PROMPT_CHAR_BUDGET", "30000"))

# 저장소
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "app.db"

# 서버
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))

# (통합 테스트 전용) UNE 계정 — 앱 런타임에서는 사용자 입력을 쓰고 저장하지 않는다
TEST_UNE_ACCOUNT = os.getenv("TEST_UNE_ACCOUNT", "")
TEST_UNE_PASSWORD = os.getenv("TEST_UNE_PASSWORD", "")


def ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
