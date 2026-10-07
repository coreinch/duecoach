"""Back-off for proactive check-ins: the longer a user stays silent, the further apart the bot's messages get.

`unanswered` counts check-ins sent since the user last wrote. The next check-in is due once the gap for that count has passed
since the last message from either side, so the bot never talks over an ongoing conversation. After the last step the bot stays
quiet until the user writes again.
"""

import time

from . import prompts
from .config import BACKOFF_MINUTES


def exhausted(unanswered: int) -> bool:
    return unanswered >= len(BACKOFF_MINUTES)


def allowed(unanswered: int, last_activity: float | None, interval_min: float, now: float | None = None) -> bool:
    """Is the next check-in due, for a user who has left this many unanswered and checks in every `interval_min` minutes?"""
    if exhausted(unanswered):
        return False
    gap = max(BACKOFF_MINUTES[unanswered], interval_min) * 60
    return (now or time.time()) - (last_activity or 0) >= gap


def instruction(unanswered: int, last_inbound: float | None, now: float | None = None) -> str | None:
    """Steering note for a check-in to someone who hasn't replied; None when they are engaged."""
    if unanswered <= 0:
        return None
    if unanswered >= len(BACKOFF_MINUTES) - 1:
        return prompts.REENGAGE_LAST
    days = int(((now or time.time()) - (last_inbound or 0)) // 86400)
    if unanswered < 3 or days < 1:
        return prompts.REENGAGE_LIGHT.format(n=unanswered)
    return prompts.REENGAGE_LONG.format(days=days)
