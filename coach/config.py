import os

from dotenv import load_dotenv

load_dotenv()

# --- channels: a channel is enabled when its credentials are set ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
VIBER_AUTH_TOKEN = os.getenv("VIBER_AUTH_TOKEN", "")
VIBER_BOT_NAME = os.getenv("VIBER_BOT_NAME", "ADHD Coach")[:28]
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
WHATSAPP_TEMPLATE = os.getenv("WHATSAPP_TEMPLATE", "")  # approved template with one {{1}} body variable

# Viber and WhatsApp deliver messages to a public HTTPS webhook served by this process.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


def normalize_ext_id(channel: str, ext_id: str) -> str:
    ext_id = str(ext_id).strip()
    return "".join(c for c in ext_id if c.isdigit()) if channel == "whatsapp" else ext_id


def _parse_allowed(raw: str) -> set[tuple[str, str]]:
    """'telegram:123, whatsapp:+3069..., viber:abc=' -> {(channel, id)}. A bare number means a Telegram id."""
    allowed = set()
    for item in raw.replace(" ", "").split(","):
        if not item:
            continue
        channel, sep, ext_id = item.partition(":")
        if not sep:
            channel, ext_id = "telegram", item
        allowed.add((channel.lower(), normalize_ext_id(channel.lower(), ext_id)))
    return allowed


# Who may use the bot. Empty = open to everyone. ALLOWED_USER_IDS / ALLOWED_USER_ID are the older Telegram-only names.
ALLOWED = _parse_allowed(os.getenv("ALLOWED_USERS") or os.getenv("ALLOWED_USER_IDS") or os.getenv("ALLOWED_USER_ID") or "")


def is_allowed(channel: str, ext_id: str) -> bool:
    return not ALLOWED or (channel, normalize_ext_id(channel, ext_id)) in ALLOWED


LLM_API_KEY = os.getenv("LLM_API_KEY", "")
if not LLM_API_KEY:
    raise SystemExit("LLM_API_KEY is not set (see .env.example)")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.kilo.ai/api/gateway")
LLM_MODEL = os.getenv("LLM_MODEL", "kilo-auto/free")
# Models to try, in order, when LLM_MODEL fails (an outage, a rate limit, a timeout, or a model that rejects a request).
# Comma-separated. They must support tool calling, since the coach reads its playbook through a tool.
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
# Seconds a model that just failed is skipped, so each message doesn't wait on a model that is down.
LLM_MODEL_COOLDOWN = int(os.getenv("LLM_MODEL_COOLDOWN", "120"))
# Stop trying further models once a request has taken this many seconds (each model can use up to LLM_TIMEOUT).
LLM_BUDGET = float(os.getenv("LLM_BUDGET", "150"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))  # seconds per model call
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))  # model calls in flight at once, across all users
TIMEZONE = os.getenv("TIMEZONE", "UTC")  # default for new users; each user can change it with /timezone
MORNING_HOUR = int(os.getenv("MORNING_HOUR", "9"))
EVENING_HOUR = int(os.getenv("EVENING_HOUR", "20"))
# Check-ins are opt-in: new users start with them off. These apply once someone turns them on (/interval on, or via the coach).
# While a user is within their daily window (/morning to /evening), the bot checks in this many minutes after the last
# message from either side. Each unanswered check-in widens the gap to the matching entry here (never below the user's interval);
# after the last entry it stays quiet until the user writes again.
CHECKIN_INTERVAL_MINUTES = int(os.getenv("CHECKIN_INTERVAL_MINUTES", "30"))
BACKOFF_MINUTES = [float(x) for x in os.getenv("BACKOFF_MINUTES", "30,60,120,240,480,1440,2880,5760,10080,20160").split(",") if x.strip()]
REVIEW_WEEKDAY = int(os.getenv("REVIEW_WEEKDAY", "6"))  # evening check-in on this weekday (Mon=0) becomes the weekly review
DB_PATH = os.getenv("DB_PATH", "coach.db")
HISTORY_TURNS = 20
# Privacy: chat messages older than this are deleted (each user's latest 40 are kept). 0 keeps everything.
# Extra text appended to the safety message sent when someone writes about suicide or self-harm, e.g. a local helpline:
# "In Greece you can also call 1018." (the message always includes the emergency number 112 and a pointer to professional help)
CRISIS_HELP = os.getenv("CRISIS_HELP", "").strip()
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))
# Abuse guard: at most this many messages per user inside the window (seconds).
RATE_LIMIT_MESSAGES = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "600"))
