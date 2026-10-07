"""The setup conversation as a whole: what follows each stage, and the one-time hand-over to normal coaching."""

from .. import db
from .moments import note_question_asked
from .state import get, put, say


def mark_onboarded(uid: int) -> None:
    db.set_field(uid, "onboarded", 1)


def handoff(uid: int) -> str:
    """The last message of the setup (shown once): how to use the bot from here, and the first thing to do with the step."""
    user = db.get_user(uid)
    if user["onboarded"]:
        return ""
    mark_onboarded(uid)
    goals, opens = db.active_goals(uid), db.open_objectives(uid)
    parts = [say(uid, "HANDOFF")]
    if opens:
        parts.append(say(uid, "HANDOFF_STEP", step=opens[0]["text"]))
    elif goals:
        parts.append(say(uid, "HANDOFF_NO_STEP"))
    else:
        parts.append(say(uid, "HANDOFF_NO_GOAL"))
    if not user["tz_set"]:
        parts.append(say(uid, "HANDOFF_NO_TZ"))
    return " ".join(parts)


def ask_timezone(uid: int, last_step_of_setup: bool = False) -> str:
    """Start the timezone flow (see timezone.py) and return the question to send."""
    put(uid, "timezone", "asked", attempts=0)
    note_question_asked(uid)
    return say(uid, "TZ_ASK_LAST" if last_step_of_setup else "TZ_ASK")


def onboarding_next(uid: int) -> str:
    """What follows a finished setup stage: the timezone question if it is still open, otherwise the hand-over; "" once all done."""
    user = db.get_user(uid)
    if user["onboarded"] or get(uid):
        return ""
    if not user["tz_set"] and not user["tz_state"]:
        return ask_timezone(uid, last_step_of_setup=True)
    return handoff(uid)


def with_next_setup_step(uid: int, reply: str) -> str:
    following = onboarding_next(uid)
    return f"{reply}\n\n{following}" if following else reply
