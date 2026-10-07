"""The timezone question, as a flow like the others (its state lives in `flow_state` too).

The bot asks for the user's city once, after a few coaching messages, because check-ins and "what time is it for you" depend on it;
the model is not involved, so it can't forget to ask or invent a zone. A zone is taken as correct as soon as it is understood: the
user is only told what local time it implies, and corrects it if wrong.

Steps: `asked` (waiting for a city), `verify` (zone assumed and in use, waiting for "yes"), `time` (told it was wrong, asking the time).
The lasting outcome is the user's `tz_state`: "" (never settled), "done" (a zone is in use) or "skipped" (they declined or it failed).
"""

from .. import db, timezones
from ..config import CHECKIN_INTERVAL_MINUTES
from .onboarding import handoff, mark_onboarded
from .state import NO, SKIP, YES, CoachTurn, clear, get, put, say

ASK_AFTER_MESSAGES = 6  # chat messages stored (both sides), i.e. about three exchanges
MAX_ANSWER_WORDS = 5  # longer messages are treated as ordinary chat, not as an answer to the question
MAX_ATTEMPTS = 3  # tries at understanding a city before giving up
MAX_CORRECTIONS = 2  # times the assumed zone may be corrected before giving up
LEADING_NO = {"no", "nope", "οχι", "λαθος"}


def pending(uid: int) -> bool:
    state = get(uid)
    return bool(state and state["flow"] == "timezone")


def should_ask(uid: int) -> bool:
    user = db.get_user(uid)
    return bool(user and not user["tz_set"] and not user["tz_state"] and db.message_count(uid) >= ASK_AFTER_MESSAGES and not get(uid))


def settle(uid: int) -> None:
    """The zone was set some other way (a command, the coach's tool): the question, if open, is over."""
    if pending(uid):
        clear(uid)
        db.set_field(uid, "tz_state", "done")


def _end(uid: int, outcome: str, reply: str) -> str:
    """The question is settled: record the outcome, and if this was the last stage of the setup, finish with the hand-over."""
    clear(uid)
    db.set_field(uid, "tz_state", outcome)
    closing = handoff(uid)
    return f"{reply}\n\n{closing}" if closing else reply


def _assume(uid: int, zone: str, corrections: int) -> str:
    """Take the zone as correct straight away and tell the user only the local time it implies, asking just whether that is right.

    The first time a timezone is set, check-ins switch on by themselves.
    """
    switched_on = db.confirm_timezone(uid, zone)
    db.set_field(uid, "tz_state", "done")
    put(uid, "timezone", "verify", candidate=zone, attempts=corrections)
    reply = say(uid, "TZ_ASSUMED", time=timezones.local_time(zone))
    if switched_on:
        reply += "\n\n" + say(uid, "CHECKINS_ENABLED", minutes=CHECKIN_INTERVAL_MINUTES)
    return reply


async def answer(uid: int, state: dict, text: str, said: str) -> str | CoachTurn | None:
    """Handle a reply to the timezone question; None means the message is ordinary chat."""
    step, attempts = state["step"], state.get("attempts", 0)
    if step == "asked":
        if db.get_user(uid)["tz_set"] or len(text.split()) > MAX_ANSWER_WORDS:
            return None
        if said in SKIP or said in NO:
            return _end(uid, "skipped", say(uid, "TZ_SKIPPED"))
        found = timezones.resolve(text)
        if found.zone:
            return _assume(uid, found.zone, corrections=0)
        if found.country:
            return say(uid, "TZ_MULTI", country=found.country)  # a follow-up question, not a failed attempt
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            return _end(uid, "skipped", say(uid, "TZ_SKIPPED"))
        put(uid, "timezone", "asked", attempts=attempts)
        return say(uid, "TZ_RETRY")
    if step == "time":  # they said the local time was wrong and were asked what time it is
        return _correct(uid, state, text)
    # step == "verify": the zone is already in use; this is the answer to "it's 16:36 where you are, right?"
    if said in YES:
        return _end(uid, "done", say(uid, "TZ_VERIFIED"))
    if said in NO:
        put(uid, "timezone", "time", candidate=state.get("candidate", ""), attempts=attempts)
        return say(uid, "TZ_ASK_TIME")
    words = text.split()
    if len(words) > 1 and timezones.normalize(words[0]) in LEADING_NO:  # "no, I'm in Dubai" / "no it's 3pm"
        return _correct(uid, state, text.split(None, 1)[1])
    found = timezones.resolve(text) if len(words) <= MAX_ANSWER_WORDS else timezones.Resolution()
    if found.zone and found.zone != db.get_user(uid)["tz"]:  # they just named a different place
        return _correct(uid, state, text)
    clear(uid)  # they moved on without objecting: the assumption stands...
    mark_onboarded(uid)  # ...and so the setup is over: no hand-over message on top of whatever they are saying
    return None


def _correct(uid: int, state: dict, text: str) -> str:
    """The assumed timezone was wrong: work out the right one from the time it is for them now, or from a place they name."""
    user = db.get_user(uid)
    corrections = state.get("attempts", 0) + 1
    clock = timezones.parse_clock(text)
    if clock:
        zone = timezones.zone_for_offset(timezones.offset_from_clock(*clock), near=user["tz"])
        if zone:
            db.confirm_timezone(uid, zone)
            return _end(uid, "done", say(uid, "TZ_FIXED", time=timezones.local_time(zone)))
    else:
        found = timezones.resolve(text)
        if found.zone and corrections <= MAX_CORRECTIONS:
            return _assume(uid, found.zone, corrections)
        if found.country:
            return say(uid, "TZ_MULTI", country=found.country)
    if corrections >= MAX_CORRECTIONS:  # we could not fix it: better no timezone (and no check-ins) than a wrong one
        db.revoke_timezone(uid)
        return _end(uid, "skipped", say(uid, "TZ_GIVE_UP"))
    put(uid, "timezone", "time", candidate=state.get("candidate", ""), attempts=corrections)
    return say(uid, "TZ_TIME_RETRY")
