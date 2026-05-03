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
LOG_PATH = os.getenv("LOG_PATH", "/host/logs/auth.log")

STATE_DIR = os.getenv("STATE_DIR", "/state")
HOST_PREFIX = os.getenv("HOST_PREFIX", "/host")
RESET_BASELINE = os.getenv("RESET_BASELINE", "false").lower() == "true"

SECURITY_FEEDS = [
    "https://www.debian.org/security/dsa",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss-analyzed.xml",
]

STACK_KEYWORDS = ["debian", "docker", "nginx", "linux", "kernel", "bookworm", "python"]
