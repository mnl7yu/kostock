import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

REPORTS_DIR = BASE_DIR / "reports"
WATCHLIST_PATH = BASE_DIR / "watchlist.json"

REPORTS_DIR.mkdir(exist_ok=True)


def validate():
    """텔레그램 필수 / Anthropic은 없으면 경고만."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise EnvironmentError(f".env에 다음 값이 없습니다: {', '.join(missing)}")
    if not ANTHROPIC_API_KEY:
        print("[config] ⚠️  ANTHROPIC_API_KEY 없음 — 데이터 요약 모드로 실행됩니다.")
