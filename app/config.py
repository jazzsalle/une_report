"""환경 설정. .env 파일과 환경변수에서 읽는다."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# LLM 생성 길이 상한 (chat completions max_tokens). 0이면 필드 미전송.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
# edit·query 프롬프트에 싣는 노드 목록의 문자 수 예산 (대형 문서 프롬프트 폭주 방지.
# 수치는 구 UNI RAG 시절 실측 기반 — T3Q에서도 안전 여유로 유지)
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

# 보고서 표준 템플릿 (서식 표본 hwpx — docs/t3q_upgrade_design.md 부록 규약)
REPORT_TEMPLATES_DIR = Path(os.getenv("REPORT_TEMPLATES_DIR", BASE_DIR / "templates"))

# 저장소
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "app.db"

# 서버
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))


def ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
