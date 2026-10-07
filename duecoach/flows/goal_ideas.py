"""Goal ideas for the goal question: worked out by the model in the background, and picking one by number."""

import asyncio

from .. import llm
from ..timezones import normalize

SUGGEST_TIMEOUT = 30  # seconds to wait for the model's goal ideas (computed on demand) before asking the open question instead


SUGGEST_PREFETCH_WAIT = 12  # ...and, when they were started earlier in the interview, how much longer to wait for them at the end


OPTION_WORDS = {
    "1": 0,
    "one": 0,
    "first": 0,
    "a": 0,
    "πρωτο": 0,
    "πρωτη": 0,
    "ενα": 0,
    "2": 1,
    "two": 1,
    "second": 1,
    "b": 1,
    "δευτερο": 1,
    "δευτερη": 1,
    "δυο": 1,
    "3": 2,
    "three": 2,
    "third": 2,
    "c": 2,
    "τριτο": 2,
    "τριτη": 2,
    "τρια": 2,
}


OPTION_FILLERS = {"option", "number", "no", "the", "idea", "αριθμος", "επιλογη", "ιδεα", "το", "τη", "την"}


_prefetched: dict[int, asyncio.Task] = {}  # user -> goal ideas being worked out in the background during the interview


def _swallow(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()  # read it, so a failed background job doesn't log "exception was never retrieved"


def prefetch_goal_options(uid: int) -> None:
    """Start working out the goal ideas now. The free model is slow (about half a minute), so this is begun as soon as the user
    has said what gets in their way: the remaining interview answers give it time to finish before the goal question is needed."""
    previous = _prefetched.pop(uid, None)
    if previous:
        previous.cancel()
    task = asyncio.create_task(llm.suggest_goals(uid))
    task.add_done_callback(_swallow)
    _prefetched[uid] = task


async def goal_options(uid: int) -> list[str]:
    """Three goal ideas drawn from what the user said, or [] if the model is slow, down or unsure (then the open question is asked)."""
    task = _prefetched.pop(uid, None)
    wait = SUGGEST_PREFETCH_WAIT if task else SUGGEST_TIMEOUT
    if task is None:
        task = asyncio.create_task(llm.suggest_goals(uid))
        task.add_done_callback(_swallow)
    await asyncio.wait({task}, timeout=wait)
    if not task.done():
        task.cancel()
        return []
    options = [] if task.cancelled() or task.exception() else task.result()
    return options if len(options) == 3 else []


def picked_option(text: str, count: int) -> tuple[int, str] | None:
    """If the reply starts by choosing a numbered idea ("2", "option 2", "the second one"): its index and any words after it."""
    words = text.split()
    skipped = 0
    while skipped < len(words) - 1 and normalize(words[skipped]) in OPTION_FILLERS:
        skipped += 1
    if not words or len(words) > 12:
        return None
    index = OPTION_WORDS.get(normalize(words[skipped]))
    if index is None or index >= count:
        return None
    rest = words[skipped + 1 :]
    if rest and normalize(rest[0]) in {"one", "idea", "option", "ενα"}:  # "the second one"
        rest = rest[1:]
    return index, " ".join(rest)
