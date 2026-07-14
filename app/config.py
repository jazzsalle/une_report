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
# fill(구조 인식 재작성)의 전량 대상 상한 노드 수. 이하면 문서 전체를
# 재작성 대상으로 삼고, 초과하면 표식·가이드 중심으로 축소한다
# (서식1급 5,386노드를 전량 순회하면 LLM 호출이 수백 회로 폭주).
FILL_NODE_LIMIT = int(os.getenv("FILL_NODE_LIMIT", "300"))

# T3Q 플랫폼 (재난안전계획서 생성 API + OpenAI 호환 LLM — docs/t3q_upgrade_design.md)
T3Q_BASE_URL = os.getenv("T3Q_BASE_URL", "https://plf.mois-disaster.t3q.ai")
# TLS: 기본은 CA 파일 검증. 실측(2026-07-14) cadm-ca.crt는 UNE 자체 CA라
# T3Q 서버(*.t3q.ai 발급) 체인과 불일치 — 올바른 CA 확보 전까지
# T3Q_TLS_VERIFY=false 우회 운용 (설계 §6, limitations 참조).
T3Q_CA_PATH = Path(os.getenv("T3Q_CA_PATH", BASE_DIR / "certs" / "cadm-ca.crt"))
T3Q_TLS_VERIFY = os.getenv("T3Q_TLS_VERIFY", "true").lower() not in ("false", "0", "no")
# 본문 생성은 섹션 수에 비례해 오래 걸린다 (실측: 2개 섹션 약 43초)
T3Q_TIMEOUT = float(os.getenv("T3Q_TIMEOUT", "600"))
T3Q_LLM_MODEL = os.getenv("T3Q_LLM_MODEL", "mois")  # API-LLM-001 고정값

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
