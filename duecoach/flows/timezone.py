"""The timezone question, as a flow (its state lives in `flow_state` too).

The bot asks for the user's city right after they agree to the privacy notice (and again after a few messages if it was never settled),
because check-ins and "what time is it for you" depend on it; the model is not involved in the asking, so it can't forget to ask or
invent a zone. A zone is taken as correct as soon as it is understood: the user is told the zone and the local time it implies. If it
is wrong they say so in ordinary chat and the coach fixes it with its set_timezone tool.

The only step is `asked` (waiting for a city). The lasting outcome is the user's `tz_state`: "" (never settled), "done" (a zone is in
use) or "skipped" (they declined or it failed).
"""

from .. import db, timezones
from ..config import CHECKIN_INTERVAL_MINUTES
from .moments import note_question_asked
from .state import NO, SKIP, clear, get, put, say

ASK_AFTER_MESSAGES = 6  # chat messages stored (both sides), i.e. about three exchanges
MAX_ANSWER_WORDS = 5  # longer messages are treated as ordinary chat, not as an answer to the question
MAX_PLACE_WORDS = 2  # an unrecognised message longer than this is chat, not a misspelt city
MAX_ATTEMPTS = 3  # tries at understanding a city before giving up


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


def ask(uid: int, key: str = "TZ_ASK") -> str:
    """Start the question and return the text to send (`key` lets /agree use its own greeting that ends with the question)."""
    put(uid, "timezone", "asked", attempts=0)
    note_question_asked(uid)
    return say(uid, key)


def _end(uid: int, outcome: str, reply: str) -> str:
    """The question is settled: record the outcome."""
    clear(uid)
    db.set_field(uid, "tz_state", outcome)
    return reply


def _assume(uid: int, zone: str) -> str:
    """Take the zone as correct straight away and tell the user the zone and the local time it implies.

    The first time a timezone is set, check-ins switch on by themselves.
    """
    switched_on = db.confirm_timezone(uid, zone)
    clear(uid)
    db.set_field(uid, "tz_state", "done")
    reply = say(uid, "TZ_ASSUMED", zone=zone, time=timezones.local_time(zone))
    if switched_on:
        reply += "\n\n" + say(uid, "CHECKINS_ENABLED", minutes=CHECKIN_INTERVAL_MINUTES)
    return reply


async def answer(uid: int, state: dict, text: str, said: str) -> str | None:
    """Handle a reply to the timezone question; None means the message is ordinary chat."""
    if state["step"] != "asked":  # a confirmation step from an earlier version of the bot: the zone is already in use
        clear(uid)
        db.set_field(uid, "tz_state", "done")
        return None
    attempts = state.get("attempts", 0)
    if db.get_user(uid)["tz_set"] or len(text.split()) > MAX_ANSWER_WORDS:
        return None
    if said in SKIP or said in NO:
        return _end(uid, "skipped", say(uid, "TZ_SKIPPED"))
    found = timezones.resolve(text)
    if found.zone:
        return _assume(uid, found.zone)
    if found.country:
        return say(uid, "TZ_MULTI", country=found.country)  # a follow-up question, not a failed attempt
    if len(text.split()) > MAX_PLACE_WORDS:
        return None  # not a place: likely the first thing they wanted to talk about, which the coach answers
    attempts += 1
    if attempts >= MAX_ATTEMPTS:
        return _end(uid, "skipped", say(uid, "TZ_SKIPPED"))
    put(uid, "timezone", "asked", attempts=attempts)
    return say(uid, "TZ_RETRY")
