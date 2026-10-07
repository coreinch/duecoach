"""Talking to the coach: one model call per message, plus at most one question of the bot's own, and the crisis answer."""

import asyncio
import logging
import re
import time

from . import db, flows, llm, prompts
from .config import CRISIS_HELP
from .flows.state import say  # noqa: F401  (the fixed texts, in the user's language; re-exported for the commands)

log = logging.getLogger("duecoach.chat")
background: set[asyncio.Task] = set()  # fire-and-forget jobs (notes refresh); shutdown waits for them
CRISIS_QUIET_SECONDS = 24 * 3600  # no proactive check-ins for a day after someone writes about harming themselves

_SENTENCE_BREAK = re.compile(r"(?<=[.!?;;])\s+")
_QUESTION_MARKS = ("?", ";", ";")  # the last one is the Greek question mark


def without_trailing_question(reply: str) -> str:
    """Drop the coach's closing question(s): when the bot adds a question of its own, the user must not get two in a row."""
    text = reply.rstrip()
    while text.endswith(_QUESTION_MARKS):
        start_of_last = max((m.end() for m in _SENTENCE_BREAK.finditer(text)), default=0)
        if start_of_last == 0:
            break  # a single sentence: keep it rather than send an empty reply
        text = text[:start_of_last].rstrip()
    return text


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
    """Reply to the question in progress (goal, weekly step, follow-up, interview, timezone), if any. None: ordinary chat."""
    result = await flows.answer(uid, text)
    if isinstance(result, flows.CoachTurn):  # the user reported how a step went, or finished the interview: coach that now
        coaching = coach(uid, result.text, result.instruction)
        if callable(result.follow_up):  # the question takes a model call too: compute it while the coach writes
            reply, question = await asyncio.gather(coaching, result.follow_up())
        else:
            reply, question = await coaching, result.follow_up
        if reply == say(uid, "LLM_ERROR") and result.fallback:
            return f"{result.fallback}\n\n{question}" if question else result.fallback
        return f"{without_trailing_question(reply)}\n\n{question}" if question else reply
    return result


async def coach_with_question(uid: int, text: str) -> str:
    """The coach's reply, plus at most one question from the bot (follow-up, goal, weekly step or timezone)."""
    # Decide before the coach writes: if a question of ours will follow, the coach must not end with one of its own.
    kind = None
    if not (flows.get(uid) or flows.question_blocked(uid, text)):
        kind = flows.question_due(uid, text) or ("timezone" if flows.should_ask_timezone(uid) else None)
    ideas = asyncio.create_task(flows.goal_options(uid)) if kind == "goal" else None  # runs while the coach writes
    reply = await coach(uid, text, prompts.QUESTION_FOLLOWS if kind else None)
    if kind is None or reply == say(uid, "LLM_ERROR"):
        if ideas:
            ideas.cancel()
        return reply
    question: str | None
    if kind == "timezone":
        question = flows.ask_timezone(uid)
    elif kind == "goal":
        assert ideas is not None
        question = await flows.start_goal(uid, options=await ideas)
    else:
        question = await flows.start_question(uid, kind, text)
    if question is None:
        return reply
    flows.note_question_asked(uid)
    return f"{without_trailing_question(reply)}\n\n{question}"


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
    flows.note_question_asked(uid)
    return reply
