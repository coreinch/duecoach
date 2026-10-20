"""Looking after the bot itself: a one-time nudge for people who stopped during setup, and a daily digest and alerts for the owner."""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import channels, db, strings
from .config import ADMIN_CHAT_ID, TIMEZONE

log = logging.getLogger("duecoach")

DIGEST_HOUR = 9  # local hour after which the day's digest goes out
ALERT_FALLBACKS = 2  # "say it again" replies in one hour that make an alert
ALERT_COOLDOWN = 6 * 3600


_last_pass = 0.0
PASS_EVERY = 600  # the maintenance loop wakes up every few seconds; these checks need not


async def run() -> None:
    global _last_pass
    if time.time() - _last_pass < PASS_EVERY:
        return
    _last_pass = time.time()
    await nudge_unfinished_setup()
    await report()


async def nudge_unfinished_setup() -> None:
    """Ask each person who stopped before finishing setup, once, whether they want to continue."""
    for user in db.users_to_nudge(time.time()):
        db.set_fields(user["user_id"], nudged_at=time.time())  # first: a failed send is not retried, so nobody is asked twice
        try:
            await channels.send(user, strings.t(user["lang"], "NUDGE_SETUP"))
        except Exception:
            log.info("could not send the setup nudge to user %s", user["user_id"], exc_info=True)


def format_digest(d: dict, hours: int) -> str:
    flags = ", ".join(f"{k} {v}" for k, v in sorted(d["flags"].items(), key=lambda kv: -kv[1])) or "none"
    return (
        f"Duecoach, last {hours} h: {d['replies']} replies (median {d['reply_p50']:.0f} s, slowest 5% over {d['reply_p95']:.0f} s), "
        f"{d['checkins']} check-ins sent, {d['withheld']} withheld. 'Say it again' fallbacks: {d['fallbacks']}. "
        f"Verifier flags: {flags}."
    )


async def tell_owner(text: str) -> None:
    log.info("owner message: %s", text)
    channel = channels.REGISTRY.get("telegram")
    if not ADMIN_CHAT_ID or channel is None:
        return
    try:
        await channel.send({"chat_id": ADMIN_CHAT_ID, "user_id": 0}, text)
    except Exception:
        log.exception("could not message the owner")


async def report(now: datetime | None = None) -> None:
    """Hourly: alert when replies keep falling back, and send the day's digest once, after DIGEST_HOUR local time."""
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    ts = now.timestamp()
    recent = db.turn_digest(ts - 3600)
    if recent["fallbacks"] >= ALERT_FALLBACKS and ts - float(db.get_meta("last_alert") or 0) > ALERT_COOLDOWN:
        db.set_meta("last_alert", str(ts))
        await tell_owner(f"Duecoach alert: {recent['fallbacks']} 'say it again' replies in the last hour. {format_digest(recent, 1)}")
    today = now.date().isoformat()
    if now.hour >= DIGEST_HOUR and db.get_meta("last_digest") != today:
        db.set_meta("last_digest", today)
        await tell_owner(format_digest(db.turn_digest(ts - 86400), 24))
