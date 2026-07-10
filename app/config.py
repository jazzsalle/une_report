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
