import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
NETDATA_BASE_URL = os.getenv("NETDATA_BASE_URL", "http://localhost:19999")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "8"))
_interval = os.getenv("REPORT_INTERVAL_HOURS", "").strip()
REPORT_INTERVAL_HOURS = int(_interval) if _interval else None
LOG_PATH = os.getenv("LOG_PATH", "/host/logs/auth.log")
RKHUNTER_LOG_PATH = os.getenv(
    "RKHUNTER_LOG_PATH",
    "/host/logs/rkhunter/reports/rkhunter-combined.log",
)

_authorized = os.getenv("TELEGRAM_AUTHORIZED_USERS", "").strip()
TELEGRAM_AUTHORIZED_USERS = [int(uid) for uid in _authorized.split(",") if uid.strip()]

STATE_DIR = os.getenv("STATE_DIR", "/state")
HOST_PREFIX = os.getenv("HOST_PREFIX", "/host")
RESET_BASELINE = os.getenv("RESET_BASELINE", "false").lower() == "true"

TTS_MODEL = os.getenv("TTS_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")
TTS_VOICE = os.getenv("TTS_VOICE", "alloy")
TTS_AS_VOICE_MESSAGE = os.getenv("TTS_AS_VOICE_MESSAGE", "true").lower() == "true"
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "3000"))
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TTS_RESPONSE_FORMAT = os.getenv("TTS_RESPONSE_FORMAT", "mp3")
TTS_PCM_SAMPLE_RATE = int(os.getenv("TTS_PCM_SAMPLE_RATE", "24000"))

SECURITY_FEEDS = [
    "https://www.debian.org/security/dsa",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss-analyzed.xml",
]

STACK_KEYWORDS = ["debian", "docker", "nginx", "linux", "kernel", "bookworm", "python"]
