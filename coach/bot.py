import asyncio
import fcntl
import logging
import os
import signal
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from aiohttp import web

from . import backoff, channels, core, db, llm, prompts, strings
from .channels.telegram import TelegramChannel
from .channels.viber import ViberChannel
from .channels.whatsapp import WhatsAppChannel
from .config import (
    DB_PATH,
    PUBLIC_URL,
    RETENTION_DAYS,
    REVIEW_WEEKDAY,
    TELEGRAM_BOT_TOKEN,
    VIBER_AUTH_TOKEN,
    VIBER_BOT_NAME,
    WEB_HOST,
    WEB_PORT,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_TEMPLATE,
    WHATSAPP_TOKEN,
    WHATSAPP_VERIFY_TOKEN,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for noisy in ("httpx", "httpx2", "httpcore"):  # request logs include URLs (the Telegram one contains the bot token)
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("coach")

LOOP_SECONDS = 30
STALE_AFTER = 4 * LOOP_SECONDS  # the health check fails when a background loop hasn't completed a pass for this long
MAINTENANCE_SECONDS = 3600
_heartbeat: dict[str, float] = {}
_last_maintenance = 0.0


# --- reminders ---


async def deliver_reminders() -> None:
    for r in db.due_reminders():
        user = db.get_user(r["user_id"])
        if user is None or not channels.can_send(user):
            continue  # e.g. WhatsApp 24h window closed: stays pending until the user writes again
        try:
            await channels.send(user, strings.t(user["lang"], "REMIND_PREFIX", text=r["text"]))
        except Exception:
            log.exception("reminder %s failed, will retry later", r["id"])
            db.mark_failed(r["id"])
            continue
        db.mark_sent(r["id"])


# --- check-ins ---


def plan_checkin(u, now_ts: float, now: datetime) -> tuple[str, dict] | None:
    """What check-in (if any) is due for this user right now: (instruction for the model, user fields to record once sent)."""
    interval, start, end, unanswered = u["interval_min"] or 0, u["morning_hour"], u["evening_hour"], u["unanswered"] or 0
    if not u["consent_at"] or interval <= 0 or start < 0 or end < 0 or now_ts < (u["snooze_until"] or 0):
        return None
    if now_ts < (u["checkin_retry_at"] or 0) or not start <= now.hour <= end:
        return None
    today = now.date().isoformat()
    if u["last_evening"] == today:
        return None  # the evening wrap-up is the day's last check-in
    last_activity = max(u["last_proactive"] or 0, u["last_inbound"] or 0)  # never talk over an ongoing conversation
    if not backoff.allowed(unanswered, last_activity, interval, now_ts):
        return None

    has_record = bool(db.active_goals(u["user_id"]) or db.open_objectives(u["user_id"]))
    days_since_review = (now.date() - date.fromisoformat(u["last_review"])).days if u["last_review"] else 99
    # weekly review: on the chosen weekday, or whenever it has become overdue (so back-off or snoozing can't skip it for weeks)
    review_due = has_record and days_since_review >= 6 and (now.weekday() == REVIEW_WEEKDAY or days_since_review >= 8)

    if unanswered > 0:
        return backoff.instruction(unanswered, u["last_inbound"], now_ts), {}
    if u["last_morning"] != today:
        return prompts.MORNING, {"last_morning": today}
    if now.hour == end and u["last_evening"] != today:
        if review_due:
            return prompts.WEEKLY_REVIEW, {"last_evening": today, "last_review": today}
        return prompts.EVENING, {"last_evening": today}
    if review_due and days_since_review >= 8:
        return prompts.WEEKLY_REVIEW, {"last_review": today}
    return prompts.PULSE, {}


async def checkin(u) -> None:
    """Send this user a check-in if one is due: every `interval_min` minutes inside their daily window, backing off if ignored."""
    uid = u["user_id"]
    try:
        now = datetime.now(ZoneInfo(u["tz"]))
    except Exception:
        return
    now_ts = time.time()
    if plan_checkin(u, now_ts, now) is None:
        return
    lock = core.user_lock(uid)
    if lock.locked():
        return  # they're mid-conversation (or a check-in is already being written)
    async with lock:
        u = db.get_user(uid)  # re-read: they may have written, or changed settings, while we waited
        if u is None:
            return
        plan = plan_checkin(u, time.time(), now)
        if plan is None:
            return
        instruction, marks = plan
        # frequent pulses must be free messages; the once-a-day and re-engagement ones may use a paid template
        if not channels.can_send(u, template_ok=instruction != prompts.PULSE):
            return
        seen_inbound = u["last_inbound"] or 0
        try:
            await channels.send(u, await llm.reply(uid, "", instruction))
        except Exception:
            log.exception("check-in failed for %s, backing off", uid)
            db.record_checkin_failed(uid)
            return
        if marks:
            db.set_fields(uid, **marks)
        db.record_checkin_sent(uid, seen_inbound)
        if backoff.exhausted((u["unanswered"] or 0) + 1):
            log.info("user %s stayed silent through every check-in; going quiet until they write", uid)


async def run_checkins() -> None:
    results = await asyncio.gather(*(checkin(u) for u in db.all_users()), return_exceptions=True)
    for error in results:
        if isinstance(error, Exception):
            log.error("check-in pass failed", exc_info=error)


# --- housekeeping ---


def maintain() -> None:
    """Hourly: expire abandoned objectives and enforce the message retention period."""
    global _last_maintenance
    if time.time() - _last_maintenance < MAINTENANCE_SECONDS:
        return
    _last_maintenance = time.time()
    expired = db.expire_stale_objectives()
    pruned = db.prune_messages(RETENTION_DAYS) if RETENTION_DAYS > 0 else 0
    if expired or pruned:
        log.info("maintenance: %d objectives expired, %d old messages deleted", expired, pruned)


async def forever(name: str, job) -> None:
    """Run `job` every LOOP_SECONDS for as long as the bot is up; one failed pass never stops the loop."""
    await asyncio.sleep(5)
    while True:
        try:
            await job()
        except Exception:
            log.exception("%s pass failed", name)
        _heartbeat[name] = time.time()
        await asyncio.sleep(LOOP_SECONDS)


async def maintenance_job() -> None:
    maintain()


async def healthz(request: web.Request) -> web.Response:
    now = time.time()
    stale = [name for name, t in _heartbeat.items() if now - t > STALE_AFTER]
    body = {"ok": not stale, "channels": list(channels.REGISTRY), "stale_loops": stale}
    return web.json_response(body, status=200 if not stale else 503)


# --- startup ---


def acquire_instance_lock():
    """Two bots on one database would double-send everything: refuse to start if another process holds the lock."""
    handle = open(os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "coach.lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another coach instance is already using this database") from None
    return handle


async def main() -> None:
    lock_handle = acquire_instance_lock()
    db.init()
    http = httpx.AsyncClient(timeout=20)
    routes: list[web.RouteDef] = [web.get("/healthz", healthz)]
    telegram = viber = None

    if TELEGRAM_BOT_TOKEN:
        telegram = TelegramChannel(TELEGRAM_BOT_TOKEN)
        channels.register(telegram)
    if VIBER_AUTH_TOKEN:
        viber = ViberChannel(VIBER_AUTH_TOKEN, VIBER_BOT_NAME, http)
        channels.register(viber)
        routes.append(web.post("/viber", viber.handle_request))
    if WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID:
        if not (WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN):
            raise SystemExit("WhatsApp needs WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN (webhooks are verified with them)")
        wa = WhatsAppChannel(WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_TEMPLATE, http)
        channels.register(wa)
        routes += [web.get("/whatsapp", wa.handle_verify), web.post("/whatsapp", wa.handle_request)]
    if not channels.REGISTRY:
        raise SystemExit("No channel configured: set TELEGRAM_BOT_TOKEN, VIBER_AUTH_TOKEN or the WHATSAPP_* variables")

    web_app = web.Application()
    web_app.add_routes(routes)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    log.info("web server listening on %s:%s", WEB_HOST, WEB_PORT)
    if telegram:
        await telegram.start()
    if viber and PUBLIC_URL:
        try:
            await viber.set_webhook(f"{PUBLIC_URL}/viber")
        except Exception:
            log.exception("could not register the Viber webhook")
    log.info("channels enabled: %s", ", ".join(channels.REGISTRY))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    # independent loops: a slow model call during check-ins must never delay reminders
    loops = [
        asyncio.create_task(forever("reminders", deliver_reminders)),
        asyncio.create_task(forever("checkins", run_checkins)),
        asyncio.create_task(forever("maintenance", maintenance_job)),
    ]
    await stop.wait()

    log.info("shutting down")
    for task in loops:
        task.cancel()
    if telegram:
        await telegram.stop()
    pending = [t for t in core._background if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=10)  # let in-flight note updates finish
    await runner.cleanup()
    await http.aclose()
    db.close()
    lock_handle.close()


if __name__ == "__main__":
    asyncio.run(main())
