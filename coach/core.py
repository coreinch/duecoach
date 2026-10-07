"""Channel-independent conversation logic: parse commands, talk to the coach, return the reply text."""

import asyncio
import logging
import re
import time
from collections import deque
from zoneinfo import ZoneInfo

from . import db, flows, llm, prompts, strings, timezones
from .config import CHECKIN_INTERVAL_MINUTES, CRISIS_HELP, RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, is_allowed
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


_SENTENCE_BREAK = re.compile(r"(?<=[.!?;\u037e])\s+")
_QUESTION_MARKS = ("?", ";", "\u037e")  # the last one is the Greek question mark


def without_trailing_question(reply: str) -> str:
    """Drop the coach's closing question(s): when the bot adds a question of its own, the user must not get two in a row."""
    text = reply.rstrip()
    while text.endswith(_QUESTION_MARKS):
        start_of_last = max((m.end() for m in _SENTENCE_BREAK.finditer(text)), default=0)
        if start_of_last == 0:
            break  # a single sentence: keep it rather than send an empty reply
        text = text[:start_of_last].rstrip()
    return text


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


OPEN_TZ_STATES = (
    "asked",
    "verify",
    "time",
)  # asked: waiting for a city; verify: assumed, waiting for "yes"; time: told "no", asking the time
MAX_TZ_CORRECTIONS = 2
LEADING_NO = {"no", "nope", "οχι", "λαθος"}


def _assume_timezone(uid: int, zone: str, corrections: int) -> str:
    """Take the zone as correct straight away and tell the user only the local time it implies, asking just whether that is right.

    The first time a timezone is set, check-ins switch on by themselves.
    """
    switched_on = db.confirm_timezone(uid, zone)
    db.set_fields(uid, tz_state="verify", tz_candidate=zone, tz_attempts=corrections)
    reply = _t(uid, "TZ_ASSUMED", time=timezones.local_time(zone))
    if switched_on:
        reply += "\n\n" + _t(uid, "CHECKINS_ENABLED", minutes=CHECKIN_INTERVAL_MINUTES)
    return reply


async def _timezone_answer(uid: int, text: str) -> str | None:
    """Handle a reply to the bot's timezone question; None means the message is ordinary chat."""
    user = db.get_user(uid)
    state = user["tz_state"]
    if state not in OPEN_TZ_STATES:
        return None
    said = timezones.normalize(text)
    if state == "asked":
        if user["tz_set"] or len(text.split()) > MAX_ANSWER_WORDS:
            return None
        if said in SKIP or said in NO:
            db.set_fields(uid, tz_state="skipped")
            return _t(uid, "TZ_SKIPPED")
        found = timezones.resolve(text)
        if found.zone:
            return _assume_timezone(uid, found.zone, corrections=0)
        if found.country:
            return _t(uid, "TZ_MULTI", country=found.country)  # a follow-up question, not a failed attempt
        attempts = (user["tz_attempts"] or 0) + 1
        if attempts >= MAX_TZ_ATTEMPTS:
            db.set_fields(uid, tz_state="skipped", tz_attempts=attempts)
            return _t(uid, "TZ_SKIPPED")
        db.set_field(uid, "tz_attempts", attempts)
        return _t(uid, "TZ_RETRY")
    if state == "time":  # they said the local time was wrong and were asked what time it is
        return await _correct_timezone(uid, text)
    # state == "verify": the zone is already in use; this is the answer to "it's 16:36 where you are, right?"
    if said in YES:
        db.set_fields(uid, tz_state="done")
        return _t(uid, "TZ_VERIFIED")
    if said in NO:
        db.set_fields(uid, tz_state="time")
        return _t(uid, "TZ_ASK_TIME")
    words = text.split()
    if words and timezones.normalize(words[0]) in LEADING_NO and len(words) > 1:  # "no, I'm in Dubai" / "no it's 3pm"
        return await _correct_timezone(uid, text.split(None, 1)[1])
    found = timezones.resolve(text) if len(words) <= MAX_ANSWER_WORDS else timezones.Resolution()
    if found.zone and found.zone != user["tz"]:  # they just named a different place
        return await _correct_timezone(uid, text)
    db.set_fields(uid, tz_state="done")  # they moved on without objecting: the assumption stands
    return None


async def _correct_timezone(uid: int, text: str) -> str:
    """The assumed timezone was wrong: work out the right one from the time it is for them now, or from a place they name."""
    user = db.get_user(uid)
    corrections = (user["tz_attempts"] or 0) + 1
    clock = timezones.parse_clock(text)
    if clock:
        zone = timezones.zone_for_offset(timezones.offset_from_clock(*clock), near=user["tz"])
        if zone:
            db.confirm_timezone(uid, zone)
            db.set_fields(uid, tz_state="done", tz_candidate="", tz_attempts=0)
            return _t(uid, "TZ_FIXED", time=timezones.local_time(zone))
    else:
        found = timezones.resolve(text)
        if found.zone and corrections <= MAX_TZ_CORRECTIONS:
            return _assume_timezone(uid, found.zone, corrections)
        if found.country:
            return _t(uid, "TZ_MULTI", country=found.country)
    if corrections >= MAX_TZ_CORRECTIONS:  # we could not fix it: better no timezone (and no check-ins) than a wrong one
        db.revoke_timezone(uid)
        db.set_fields(uid, tz_state="skipped", tz_candidate="", tz_attempts=corrections)
        return _t(uid, "TZ_GIVE_UP")
    db.set_fields(uid, tz_state="time", tz_attempts=corrections)
    return _t(uid, "TZ_TIME_RETRY")


def _timezone_pending(uid: int) -> bool:
    user = db.get_user(uid)
    return bool(user and user["tz_state"] in OPEN_TZ_STATES)


def _should_ask_timezone(uid: int) -> bool:
    user = db.get_user(uid)
    return bool(user and not user["tz_set"] and not user["tz_state"] and db.message_count(uid) >= ASK_TIMEZONE_AFTER_MESSAGES)


async def _flow_answer(uid: int, text: str) -> str | None:
    """Reply to the goal / weekly-step / follow-up question in progress, if any. None: ordinary chat."""
    result = await flows.answer(uid, text)
    if isinstance(result, flows.CoachTurn):  # the user reported how a step went, or finished the interview: coach that now
        reply = await _coach(uid, result.text, result.instruction)
        if reply == _t(uid, "LLM_ERROR") and result.fallback:
            return result.fallback
        return f"{without_trailing_question(reply)}\n\n{result.follow_up}" if result.follow_up else reply
    return result


async def _coach_with_question(uid: int, text: str) -> str:
    """The coach's reply, plus at most one question from the bot (follow-up, goal, weekly step or timezone)."""
    # Decide before the coach writes: if a question of ours will follow, the coach must not end with one of its own.
    kind = None
    if not (flows.get(uid) or _timezone_pending(uid) or flows.question_blocked(uid, text)):
        kind = flows.question_due(uid, text) or ("timezone" if _should_ask_timezone(uid) else None)
    reply = await _coach(uid, text, prompts.QUESTION_FOLLOWS if kind else None)
    if kind is None or reply == _t(uid, "LLM_ERROR") or flows.distress_in_reply(uid):
        return reply
    if kind == "timezone":
        db.set_field(uid, "tz_state", "asked")
        question = _t(uid, "TZ_ASK")
    else:
        question = await flows.start_question(uid, kind, text)
    if question is None:
        return reply
    flows.note_question_asked(uid)
    return f"{without_trailing_question(reply)}\n\n{question}"


async def _goal(uid, args):
    if len(db.active_goals(uid)) >= db.MAX_GOALS:
        return _t(uid, "GOAL_FULL")
    return flows.start_goal(uid)


async def _step(uid, args):
    return flows.start_objective(uid)


CRISIS_QUIET_SECONDS = 24 * 3600  # no proactive check-ins for a day after someone writes about harming themselves


async def _crisis(uid: int, text: str) -> str:
    """Someone wrote about suicide or self-harm: a fixed, immediate safety message that does not depend on the model.

    Any question the bot had open is dropped (it must never treat this as an answer), proactive check-ins pause, and no new bot
    question is asked for a while. The exchange is stored, so the coach has the context when the conversation continues.
    """
    flows.clear(uid)
    user = db.get_user(uid)
    if user["tz_state"] == "asked":
        db.set_fields(uid, tz_state="", tz_candidate="")  # the question can be asked again later
    elif user["tz_state"] in ("verify", "time"):
        db.set_fields(uid, tz_state="done", tz_candidate="")  # the zone in use stays; correcting it can wait
    reply = _t(uid, "CRISIS", help=f" {CRISIS_HELP}" if CRISIS_HELP else "")
    db.add_message(uid, "user", text)
    db.add_message(uid, "assistant", reply)
    db.set_field(uid, "snooze_until", time.time() + CRISIS_QUIET_SECONDS)
    flows.note_question_asked(uid)
    return reply


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


def _profile_text(uid: int) -> str:
    profile = db.get_profile(uid)
    lines = [f"{_t(uid, f'PROFILE_{topic}')}: {profile[topic]}" for topic in flows.INTAKE_STEPS if profile.get(topic)]
    return f"{_t(uid, 'PROFILE_HEADER')}:\n" + "\n".join(lines) if lines else ""


async def _notes(uid, args):
    parts = [part for part in (_profile_text(uid), db.get_notes(uid)) if part]
    return "\n\n".join(parts) or _t(uid, "NOTES_EMPTY")


async def _intake(uid, args):
    return flows.start_intake(uid, restart=True)


async def _forget(uid, args):
    db.set_fields(uid, notes="", profile="")
    return _t(uid, "NOTES_CLEARED")


async def _timezone(uid, args):
    if not args:
        return _t(uid, "TZ_SHOW", tz=db.get_user(uid)["tz"])
    try:
        ZoneInfo(args[0])
    except Exception:
        return _t(uid, "TZ_UNKNOWN")
    switched_on = db.confirm_timezone(uid, args[0])
    if db.get_user(uid)["tz_state"] in OPEN_TZ_STATES:
        db.set_fields(uid, tz_state="done", tz_candidate="")  # answered by command: the question is over
    note = "\n\n" + _t(uid, "CHECKINS_ENABLED", minutes=CHECKIN_INTERVAL_MINUTES) if switched_on else ""
    return _t(uid, "TZ_SET", tz=args[0]) + note


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
    "intake": _intake,
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
            if command is None and flows.is_crisis(text):
                # safety first, ahead of the privacy notice; nothing is stored or sent to a model
                return _t(uid, "CRISIS", help=f" {CRISIS_HELP}" if CRISIS_HELP else "")
            if command == "agree":
                return await _agree(uid, args)
            if command in BEFORE_CONSENT:
                return await COMMANDS[command](uid, args) if command not in ("start", "help") else _t(uid, "PRIVACY")
            return _t(uid, "PRIVACY")
        handler = COMMANDS.get(command) if command else None
        if handler:
            return await handler(uid, args)
        if flows.is_crisis(text):
            return await _crisis(uid, text)  # before any pending question can mistake this for its answer
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
