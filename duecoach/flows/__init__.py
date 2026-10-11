"""The one code-led flow: the timezone question. Everything else in the conversation is the model's.

The bot asks, the model is not involved, and nothing is saved until the user's answer is understood. That keeps the timezone
reliable (check-ins depend on it): the model can't forget to ask or invent a zone.

A flow's state is one small JSON value on the user row (`flow_state`, see state.py). It expires after a day, so an unanswered
question never blocks anything for long. This module dispatches an answer to the flow that asked, and re-exports the pieces the
rest of the bot uses.
"""

from ..timezones import normalize
from .moments import is_crisis
from .state import FLOW_TTL, clear, get, put
from .timezone import answer as timezone_answer
from .timezone import ask as ask_timezone
from .timezone import pending as timezone_pending
from .timezone import settle as settle_timezone

__all__ = [
    "FLOW_TTL",
    "answer",
    "ask_timezone",
    "clear",
    "get",
    "is_crisis",
    "put",
    "settle_timezone",
    "timezone_pending",
]


async def answer(uid: int, text: str) -> str | None:
    """Handle the user's reply to the question in progress. None: it was ordinary chat, let the coach answer."""
    state = get(uid)
    if state is None:
        return None
    if state["flow"] != "timezone":  # a question of a flow that no longer exists, left over from an earlier version
        clear(uid)
        return None
    return await timezone_answer(uid, state, text, normalize(text))
