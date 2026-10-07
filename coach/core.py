"""Channel-independent conversation logic: parse commands, talk to the coach, return the reply text."""

import asyncio
import logging
import time
from collections import deque
from zoneinfo import ZoneInfo

from . import db, flows, llm, prompts, strings, timezones
from .config import CHECKIN_INTERVAL_MINUTES, RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, is_allowed
from .flows import NO, SKIP, YES

log = logging.getLogger("coach.core")
_background: set[asyncio.Task] = set()
_locks: dict[int, asyncio.Lock] = {}
_recent: dict[int, deque] = {}  # per user: timestamps of recent messages, for the rate limit
_warned: set[int] = set()

# Timezone onboarding. The bot asks for the user's city once, after a few coaching messages, because check-ins and "what time is
# it for you" depend on it; the model is not involved, so it can't forget to ask or invent a zone.
ASK_TIMEZONE_AFTER_MESSAGES = 6  # chat messages stored (both sides), i.e. about three exchanges
MAX_ANSWER_WORDS = 5  # longer messages are treated as ordinary chat, not as an answer to the question
MAX_TZ_ATTEMPTS = 3


def user_lock(uid: int) -> asyncio.Lock:
    """One conversation at a time per user: messages, commands and proactive check-ins all take this lock."""
    return _locks.setdefault(uid, asyncio.Lock())


def _t(uid: int, key: str, **kw) -> str:
    return strings.t(db.get_user(uid)["lang"], key, **kw)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _refresh_notes(uid: int) -> None:
    try:
        await llm.refresh_notes(uid)
    except Exception:
        log.exception("notes refresh failed for %s", uid)


async def _coach(uid: int, text: str, instruction: str | None = None, coach: bool = True) -> str:
    try:
        out = await llm.reply(uid, text, instruction, coach=coach)
    except Exception:
        log.exception("LLM call failed")
        return _t(uid, "LLM_ERROR")
    _spawn(_refresh_notes(uid))  # after the reply is on its way, so it never delays the user
    return out


# --- commands: async fn(uid, args) -> reply text ---


async def _timezone_answer(uid: int, text: str) -> str | None:
    """Handle a reply to the bot's timezone question; None means the message is ordinary chat."""
    user = db.get_user(uid)
    if user["tz_set"] or user["tz_state"] not in ("asked", "confirm"):
        return None
    said = timezones.normalize(text)
    if user["tz_state"] == "confirm":
        if said in YES:
            zone = user["tz_candidate"]
            db.set_fields(uid, tz=zone, tz_set=1, tz_state="done", tz_candidate="")
            return _t(uid, "TZ_DONE", zone=zone)
        if said in NO:
            db.set_fields(uid, tz_state="asked", tz_candidate="")
            return _t(uid, "TZ_NO")
        return None  # they moved on to something else; the question stays open
    words = text.split()
    if len(words) > MAX_ANSWER_WORDS:
        return None
    if said in SKIP or said in NO:
        db.set_fields(uid, tz_state="skipped")
        return _t(uid, "TZ_SKIPPED")
    found = timezones.resolve(text)
    if found.zone:
        db.set_fields(uid, tz_state="confirm", tz_candidate=found.zone)
        return _t(uid, "TZ_CONFIRM", zone=found.zone, time=timezones.local_time(found.zone))
    if found.country:
        return _t(uid, "TZ_MULTI", country=found.country)  # a follow-up question, not a failed attempt
    attempts = (user["tz_attempts"] or 0) + 1
    if attempts >= MAX_TZ_ATTEMPTS:
        db.set_fields(uid, tz_state="skipped", tz_attempts=attempts)
        return _t(uid, "TZ_SKIPPED")
    db.set_field(uid, "tz_attempts", attempts)
    return _t(uid, "TZ_RETRY")


def _timezone_pending(uid: int) -> bool:
    user = db.get_user(uid)
    return bool(user and not user["tz_set"] and user["tz_state"] in ("asked", "confirm"))


def _should_ask_timezone(uid: int) -> bool:
    user = db.get_user(uid)
    return bool(user and not user["tz_set"] and not user["tz_state"] and db.message_count(uid) >= ASK_TIMEZONE_AFTER_MESSAGES)


async def _flow_answer(uid: int, text: str) -> str | None:
    """Reply to the goal / weekly-step / follow-up question in progress, if any. None: ordinary chat."""
    result = await flows.answer(uid, text)
    if isinstance(result, flows.CoachTurn):  # the user reported how a step went: coach that now
        return await _coach(uid, result.text, result.instruction)
    return result


async def _coach_with_question(uid: int, text: str) -> str:
    """The coach's reply, plus at most one question from the bot (follow-up, goal, weekly step or timezone)."""
    reply = await _coach(uid, text)
    if reply == _t(uid, "LLM_ERROR") or flows.get(uid) or _timezone_pending(uid) or flows.question_blocked(uid, text):
        return reply
    question = await flows.next_question(uid, text)
    if question is None and _should_ask_timezone(uid):
        db.set_field(uid, "tz_state", "asked")
        question = _t(uid, "TZ_ASK")
    if question is None:
        return reply
    flows.note_question_asked(uid)
    return f"{reply}\n\n{question}"


async def _goal(uid, args):
    if len(db.active_goals(uid)) >= db.MAX_GOALS:
        return _t(uid, "GOAL_FULL")
    return flows.start_goal(uid)


async def _step(uid, args):
    return flows.start_objective(uid)


async def _help(uid, args):
    return _t(uid, "HELP")


async def _stuck(uid, args):
    task = " ".join(args)
    return await _coach(uid, f"/stuck {task}".strip(), prompts.STUCK.format(task=f" on: {task}" if task else ""))


async def _plan(uid, args):
    extra = " ".join(args)
    return await _coach(uid, f"/plan {extra}".strip(), prompts.PLAN.format(extra=f". Context: {extra}" if extra else ""))


async def _overwhelm(uid, args):
    extra = " ".join(args)
    return await _coach(uid, f"/overwhelm {extra}".strip(), prompts.OVERWHELM.format(extra=f": {extra}" if extra else ""))


async def _remind(uid, args):
    try:
        minutes = float(args[0])
        if not 0 < minutes <= 60 * 24 * 30:
            raise ValueError
    except (IndexError, ValueError):
        return _t(uid, "REMIND_USAGE")
    db.add_reminder(uid, time.time() + minutes * 60, " ".join(args[1:]) or _t(uid, "REMIND_DEFAULT"))
    return _t(uid, "REMIND_OK", minutes=minutes)


async def _goals(uid, args):
    goals, objectives = db.active_goals(uid), db.open_objectives(uid)
    parts = [
        f"{_t(uid, 'GOALS_HEADER')}:\n" + "\n".join(f"{i}. {g['text']}" for i, g in enumerate(goals, 1)) if goals else _t(uid, "GOALS_NONE")
    ]
    if goals or objectives:
        parts.append(
            f"{_t(uid, 'OBJ_HEADER')}:\n"
            + "\n".join(f"• {o['text']}" + (f" ({o['incentive']})" if o["incentive"] else "") for o in objectives)
            if objectives
            else _t(uid, "OBJ_NONE")
        )
    return "\n\n".join(parts)


async def _toolbox(uid, args):
    items = db.toolbox(uid)
    if not items:
        return _t(uid, "TOOLBOX_EMPTY")
    return f"{_t(uid, 'TOOLBOX_HEADER')}:\n" + "\n".join(f"• {t['text']}" for t in items)


async def _progress(uid, args):
    return await _coach(uid, "/progress", prompts.PROGRESS, coach=False)  # a written note, not a coaching turn


async def _notes(uid, args):
    return db.get_notes(uid) or _t(uid, "NOTES_EMPTY")


async def _forget(uid, args):
    db.set_field(uid, "notes", "")
    return _t(uid, "NOTES_CLEARED")


async def _timezone(uid, args):
    if not args:
        return _t(uid, "TZ_SHOW", tz=db.get_user(uid)["tz"])
    try:
        ZoneInfo(args[0])
    except Exception:
        return _t(uid, "TZ_UNKNOWN")
    db.set_fields(uid, tz=args[0], tz_set=1)
    return _t(uid, "TZ_SET", tz=args[0])


async def _privacy(uid, args):
    return _t(uid, "PRIVACY")


async def _agree(uid, args):
    db.set_field(uid, "consent_at", time.time())
    return _t(uid, "AGREED")


async def _deletedata(uid, args):
    if args[:1] != ["confirm"]:
        return _t(uid, "DELETE_CONFIRM")
    message = _t(uid, "DELETED")  # read the language before the user row disappears
    db.delete_user_data(uid)
    _recent.pop(uid, None)
    return message


async def _language(uid, args):
    if not args:
        return _t(uid, "LANG_SHOW", name=strings.LANGUAGES[db.get_user(uid)["lang"]])
    code = args[0].lower()
    if code not in strings.LANGUAGES:
        return _t(uid, "LANG_BAD")
    db.set_field(uid, "lang", code)
    return _t(uid, "LANG_SET", name=strings.LANGUAGES[code])


def _checkin(name: str):
    async def run(uid, args):
        field, label = f"{name}_hour", _t(uid, f"LABEL_{name}")
        if not args:
            h = db.get_user(uid)[field]
            return _t(uid, "CHECKIN_SHOW_OFF" if h < 0 else "CHECKIN_SHOW_ON", label=label, name=name, hour=h)
        arg = args[0].lower()
        if arg == "off":
            hour = -1
        elif arg.isdigit() and 0 <= int(arg) <= 23:
            hour = int(arg)
        else:
            return _t(uid, "CHECKIN_BAD", name=name)
        user = db.get_user(uid)
        start, end = (hour, user["evening_hour"]) if name == "morning" else (user["morning_hour"], hour)
        if hour >= 0 and 0 <= start and 0 <= end and start >= end:
            return _t(uid, "CHECKIN_ORDER")
        db.set_field(uid, field, hour)
        if hour < 0:
            return _t(uid, "CHECKIN_OFF", label=label, name=name)
        return _t(uid, "CHECKIN_SET", label=label, hour=hour, tz=db.get_user(uid)["tz"])

    return run


async def _interval(uid, args):
    if not args:
        minutes = db.get_user(uid)["interval_min"] or 0
        return _t(uid, "INTERVAL_SHOW", minutes=minutes) if minutes else _t(uid, "INTERVAL_OFF")
    arg = args[0].lower()
    if arg == "off":
        db.set_field(uid, "interval_min", 0)
        return _t(uid, "INTERVAL_OFF")
    minutes = CHECKIN_INTERVAL_MINUTES if arg == "on" else int(arg) if arg.isdigit() else 0
    if not 15 <= minutes <= 240:
        return _t(uid, "INTERVAL_BAD")
    if not db.get_user(uid)["tz_set"]:
        return _t(uid, "NEED_TZ")  # check-ins run all day: a wrong timezone would mean pings in the middle of the night
    db.set_field(uid, "interval_min", minutes)
    return _t(uid, "INTERVAL_SET", minutes=minutes)


COMMANDS = {
    "start": _help,
    "help": _help,
    "stuck": _stuck,
    "plan": _plan,
    "overwhelm": _overwhelm,
    "remind": _remind,
    "notes": _notes,
    "forget": _forget,
    "goals": _goals,
    "toolbox": _toolbox,
    "progress": _progress,
    "timezone": _timezone,
    "language": _language,
    "interval": _interval,
    "goal": _goal,
    "step": _step,
    "morning": _checkin("morning"),
    "evening": _checkin("evening"),
    "privacy": _privacy,
    "agree": _agree,
    "deletedata": _deletedata,
}
BEFORE_CONSENT = {"start", "help", "privacy", "agree", "language"}  # everything else waits until the user has agreed


def _user_for(channel: str, ext_id: str, chat_id: str, lang_hint: str):
    """The user row, or None if this identity isn't allowed to use the bot."""
    if not is_allowed(channel, ext_id):
        log.info("ignored message from unauthorised %s:%s", channel, ext_id)
        return None
    user = db.get_or_create_user(channel, ext_id, chat_id, lang_hint)
    db.set_fields(user["user_id"], last_inbound=time.time(), unanswered=0)  # they wrote, so check-ins return to normal
    return user


def _rate_limited(uid: int) -> bool:
    """Sliding-window limit on messages per user; True means drop this message."""
    now = time.time()
    window = _recent.setdefault(uid, deque())
    while window and now - window[0] > RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT_MESSAGES:
        return True
    window.append(now)
    _warned.discard(uid)
    return False


async def handle_text(channel: str, ext_id: str, chat_id: str, text: str, lang_hint: str = "en") -> str | None:
    """Process one inbound text message and return the reply to send (None = send nothing)."""
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    text = text.strip()
    if user is None or not text:
        return None
    uid = user["user_id"]
    if _rate_limited(uid):
        if uid in _warned:
            return None
        _warned.add(uid)
        return _t(uid, "RATE_LIMITED")
    command, args = None, []
    if text.startswith("/"):
        word, *args = text.split()
        command = word[1:].split("@")[0].lower()
    async with user_lock(uid):
        if not user["consent_at"]:
            # health-related chat goes to a third-party AI provider: nothing is processed before the user has agreed
            if command == "agree":
                return await _agree(uid, args)
            if command in BEFORE_CONSENT:
                return await COMMANDS[command](uid, args) if command not in ("start", "help") else _t(uid, "PRIVACY")
            return _t(uid, "PRIVACY")
        handler = COMMANDS.get(command) if command else None
        if handler:
            return await handler(uid, args)
        if (answer := await _flow_answer(uid, text)) is not None:
            return answer
        if (answer := await _timezone_answer(uid, text)) is not None:
            return answer
        return await _coach_with_question(uid, text)


async def handle_unsupported(channel: str, ext_id: str, chat_id: str, lang_hint: str = "en") -> str | None:
    """Inbound that isn't text (voice note, image, ...)."""
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    if user is None:
        return None
    return _t(user["user_id"], "UNSUPPORTED" if user["consent_at"] else "PRIVACY")
