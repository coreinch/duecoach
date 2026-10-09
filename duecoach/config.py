import os

from dotenv import load_dotenv

load_dotenv()


def _number(name: str, default: float, kind: type = int, low: float | None = None, high: float | None = None):
    """A numeric setting from the environment; a typo stops the start-up with a message that names the setting."""
    raw = os.getenv(name, "").strip()
    try:
        value = kind(raw) if raw else kind(default)
    except ValueError:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from None
    if (low is not None and value < low) or (high is not None and value > high):
        raise SystemExit(f"{name} must be between {low} and {high}, got {value}")
    return value


# --- channels: a channel is enabled when its credentials are set ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
VIBER_AUTH_TOKEN = os.getenv("VIBER_AUTH_TOKEN", "")
VIBER_BOT_NAME = os.getenv("VIBER_BOT_NAME", "Duecoach")[:28]
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
WHATSAPP_TEMPLATE = os.getenv("WHATSAPP_TEMPLATE", "")  # approved template with one {{1}} body variable

# Viber and WhatsApp deliver messages to a public HTTPS webhook served by this process.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = _number("WEB_PORT", 8080, low=1, high=65535)


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
LLM_MODEL = os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
# Models to try, in order, when LLM_MODEL fails (an outage, a rate limit, a timeout, or a model that rejects a request).
# Comma-separated. They must support tool calling (reminders, goals and the like are tools).
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
# Automatic model selection (see modelpicker.py): every LLM_REFRESH_HOURS the gateway's free models are probed and the fastest ones
# that answer in clean Greek and call tools correctly become the model list. 0 turns it off (only LLM_MODEL and the fallbacks are used).
# LLM_MODEL / LLM_FALLBACK_MODELS stay as the seed before the first check and as the last resort behind the picked models.
LLM_REFRESH_HOURS = _number("LLM_REFRESH_HOURS", 6, float, low=0, high=None)
LLM_POOL_SIZE = _number("LLM_POOL_SIZE", 4, low=1, high=10)  # how many picked models to keep, fastest first
LLM_MIN_CONTEXT = _number("LLM_MIN_CONTEXT", 32000, low=1000, high=None)  # tokens a candidate must accept
# Candidates whose id matches this regex are never probed (code, safety, audio and similar models).
LLM_MODEL_DENYLIST = os.getenv("LLM_MODEL_DENYLIST", r"code|safety|guard|moderat|embed|rerank|lyria|image|audio|tts|vision|ocr")
# Seconds a model that just failed is skipped, so each message doesn't wait on a model that is down.
LLM_MODEL_COOLDOWN = _number("LLM_MODEL_COOLDOWN", 120, low=0, high=None)
# Stop trying further models once a request has taken this many seconds (each model can use up to LLM_TIMEOUT).
LLM_BUDGET = _number("LLM_BUDGET", 150, float, low=1, high=None)
LLM_TIMEOUT = _number("LLM_TIMEOUT", 60, float, low=1, high=None)  # seconds per model call
LLM_CONCURRENCY = _number("LLM_CONCURRENCY", 4, low=1, high=None)  # model calls in flight at once, across all users
TIMEZONE = os.getenv("TIMEZONE", "UTC")  # default for new users; each user can change it with /timezone
MORNING_HOUR = _number("MORNING_HOUR", 9, low=0, high=23)
EVENING_HOUR = _number("EVENING_HOUR", 20, low=0, high=23)
# Check-ins are opt-in: new users start with them off. These apply once someone turns them on (/interval on, or via the coach).
# While a user is within their daily window (/morning to /evening), the bot checks in this many minutes after the last
# message from either side. Each unanswered check-in widens the gap to the matching entry here (never below the user's interval);
# after the last entry it stays quiet until the user writes again.
CHECKIN_INTERVAL_MINUTES = _number("CHECKIN_INTERVAL_MINUTES", 30, low=1, high=None)
try:
    BACKOFF_MINUTES = [
        float(x) for x in os.getenv("BACKOFF_MINUTES", "30,60,120,240,480,1440,2880,5760,10080,20160").split(",") if x.strip()
    ]
except ValueError:
    raise SystemExit("BACKOFF_MINUTES must be comma-separated numbers") from None
REVIEW_WEEKDAY = _number("REVIEW_WEEKDAY", 6, low=0, high=6)  # evening check-in on this weekday (Mon=0) becomes the weekly review
DB_PATH = os.getenv("DB_PATH", "duecoach.db")
HISTORY_TURNS = 20
# Privacy: chat messages older than this are deleted (each user's latest 40 are kept). 0 keeps everything.
# Extra text appended to the safety message sent when someone writes about suicide or self-harm, e.g. a local helpline:
# "In Greece you can also call 1018." (the message always includes the emergency number 112 and a pointer to professional help)
CRISIS_HELP = os.getenv("CRISIS_HELP", "").strip()
RETENTION_DAYS = _number("RETENTION_DAYS", 90, low=0, high=None)
# Everything stored about someone who has not written for this many days is deleted (0 keeps it for good).
INACTIVE_DELETE_DAYS = _number("INACTIVE_DELETE_DAYS", 365, low=0, high=None)
# A reminder that could not be delivered is dropped once it is this many hours overdue.
REMINDER_GIVE_UP_HOURS = _number("REMINDER_GIVE_UP_HOURS", 24, low=0, high=None)
# Abuse guard: at most this many messages per user inside the window (seconds).
RATE_LIMIT_MESSAGES = _number("RATE_LIMIT_MESSAGES", 20, low=1, high=None)
RATE_LIMIT_WINDOW = _number("RATE_LIMIT_WINDOW", 600, low=1, high=None)
