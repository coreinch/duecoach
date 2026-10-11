"""Talking to the coach: one model call per message, and the crisis answer."""

import asyncio
import logging
import time

from . import db, flows, llm
from .config import CRISIS_HELP, INACTIVE_DELETE_DAYS, RETENTION_DAYS
from .flows.state import say  # noqa: F401  (the fixed texts, in the user's language; re-exported for the commands)

log = logging.getLogger("duecoach.chat")
background: set[asyncio.Task] = set()  # fire-and-forget jobs (notes refresh); shutdown waits for them
CRISIS_QUIET_SECONDS = 24 * 3600  # no proactive check-ins for a day after someone writes about harming themselves


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


async def _refresh_notes(uid: int) -> None:
    try:
        await llm.refresh_notes(uid)
    except Exception:
        log.exception("notes refresh failed for %s", uid)


async def coach(uid: int, text: str, instruction: str | None = None) -> str:
    """The coach's answer to `text` (the fixed apology if the model is unavailable)."""
    try:
        out = await llm.reply(uid, text, instruction)
    except Exception:
        log.exception("LLM call failed")
        return say(uid, "LLM_ERROR")
    _spawn(_refresh_notes(uid))  # after the reply is on its way, so it never delays the user
    return out


async def flow_answer(uid: int, text: str) -> str | None:
    """Reply to the timezone question in progress, if any. None: ordinary chat."""
    return await flows.answer(uid, text)


def privacy(uid: int) -> str:
    """The privacy notice, with the retention periods this deployment actually uses (0 means kept for good, so not mentioned)."""
    keeping = ""
    if RETENTION_DAYS > 0:
        keeping += say(uid, "KEEP_MESSAGES", days=RETENTION_DAYS)
    if INACTIVE_DELETE_DAYS > 0:
        keeping += say(uid, "KEEP_INACTIVE", days=INACTIVE_DELETE_DAYS)
    return say(uid, "PRIVACY", keeping=keeping)


def crisis_message(uid: int) -> str:
    """The fixed safety message (emergency number, professional help, and the local helpline if one is configured)."""
    return say(uid, "CRISIS", help=f" {CRISIS_HELP}" if CRISIS_HELP else "")


async def crisis(uid: int, text: str) -> str:
    """Someone wrote about suicide or self-harm: a fixed, immediate safety message that does not depend on the model.

    Any question the bot had open is dropped (it must never treat this as an answer; a timezone question can be asked again later,
    and a zone already in use stays), proactive check-ins pause, and no new bot question is asked for a while. The exchange is
    stored, so the coach has the context when the conversation continues.
    """
    flows.clear(uid)
    reply = crisis_message(uid)
    db.add_message(uid, "user", text)
    db.add_message(uid, "assistant", reply)
    db.set_field(uid, "snooze_until", time.time() + CRISIS_QUIET_SECONDS)
    return reply
