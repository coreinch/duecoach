"""A sliding-window limit on messages per user, so one chat can't run up the model bill."""

import time
from collections import deque

from .config import RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW

_recent: dict[int, deque] = {}  # per user: timestamps of recent messages
warned: set[int] = set()  # users who have already been told to slow down (told once per burst)


def limited(uid: int) -> bool:
    """True means drop this message."""
    now = time.time()
    window = _recent.setdefault(uid, deque())
    while window and now - window[0] > RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT_MESSAGES:
        return True
    window.append(now)
    warned.discard(uid)
    return False


def forget(uid: int) -> None:
    _recent.pop(uid, None)


def reset() -> None:
    _recent.clear()
    warned.clear()
