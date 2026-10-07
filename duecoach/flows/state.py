"""Where a flow is stored (one JSON value on the user row), the yes/no/skip word lists, and what a finished flow hands back."""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .. import db, strings

FLOW_TTL = 24 * 3600


STOP = {"stop", "enough", "no more", "that s enough", "that s all", "αρκετα", "σταματα", "τελος", "οχι αλλο", "δεν θελω αλλες"}


YES = {"yes", "y", "yeah", "yep", "yup", "correct", "right", "ok", "okay", "sure", "ναι", "ν", "σωστα", "σωστο", "ενταξει", "οκ"}


NO = {"no", "n", "nope", "wrong", "incorrect", "no thanks", "οχι", "λαθος", "οχι ευχαριστω"}


SKIP = {"skip", "later", "pass", "not now", "skip it", "παραληψη", "αργοτερα", "οχι τωρα", "δεν θελω"}


NONE_WORDS = {"none", "no", "nothing", "skip", "no reward", "κανενα", "τιποτα", "οχι", "παραληψη", "χωρις"}


@dataclass
class CoachTurn:
    """The flow is over and the coach should now respond to what the user just reported."""

    text: str
    instruction: str
    follow_up: "str | Callable[[], Awaitable[str]]" = (
        ""  # a question of the bot's to add after the coach's reply (may be computed alongside it)
    )
    fallback: str = ""  # sent instead of the coach's reply (still followed by the question) if the model is unavailable


def say(uid: int, key: str, **kw) -> str:
    return strings.t(db.get_user(uid)["lang"], key, **kw)


def get(uid: int) -> dict | None:
    """The flow in progress, or None. Unreadable or day-old state is discarded."""
    user = db.get_user(uid)
    raw = user["flow_state"] if user else ""
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        state = None
    if not state or time.time() - state.get("started", 0) > FLOW_TTL:
        clear(uid)
        return None
    return state


def put(uid: int, flow: str, step: str, started: float | None = None, **data) -> None:
    previous = get(uid)
    started = started or (previous["started"] if previous and previous["flow"] == flow else time.time())
    db.set_field(uid, "flow_state", json.dumps({"flow": flow, "step": step, "started": started, **data}))


def clear(uid: int) -> None:
    db.set_field(uid, "flow_state", "")


def word_count(text: str) -> int:
    return len(text.split())
