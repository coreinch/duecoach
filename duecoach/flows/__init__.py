"""Code-led coaching flows: set a goal, pick this week's small step (and a reward), report how it went, get to know the user.

The bot asks, the model only helps with wording, and nothing is saved until the user says yes. That keeps the coaching record
reliable: the free models forget to ask, save duplicates and claim to have saved things they haven't.

A flow's state is one small JSON value on the user row (`flow_state`, see state.py). It expires after a day, so an unanswered
question never blocks anything for long. This module dispatches an answer to the flow that asked, and re-exports the pieces the
rest of the bot uses.
"""

from ..timezones import normalize
from .followup import barrier_answer, outcome_answer, start_followup
from .goal_ideas import goal_options, prefetch_goal_options
from .goals import confirming, drafting, offer_step, reward_answer, start_goal, start_objective
from .intake import INTAKE_STEPS, intake_answer, start_intake
from .moments import (
    GOAL_FALLBACK_MESSAGES,
    GOAL_MIN_MESSAGES,
    INTAKE_COOLDOWN,
    QUESTION_GAP,
    is_crisis,
    note_question_asked,
    question_blocked,
    question_due,
    signal_in,
)
from .onboarding import ask_timezone, handoff, mark_onboarded
from .state import FLOW_TTL, CoachTurn, clear, get, put
from .timezone import answer as timezone_answer
from .timezone import pending as timezone_pending
from .timezone import settle as settle_timezone
from .timezone import should_ask as should_ask_timezone

__all__ = [
    "FLOW_TTL",
    "GOAL_FALLBACK_MESSAGES",
    "GOAL_MIN_MESSAGES",
    "INTAKE_COOLDOWN",
    "INTAKE_STEPS",
    "QUESTION_GAP",
    "CoachTurn",
    "answer",
    "ask_timezone",
    "clear",
    "get",
    "goal_options",
    "handoff",
    "is_crisis",
    "mark_onboarded",
    "note_question_asked",
    "prefetch_goal_options",
    "put",
    "question_blocked",
    "question_due",
    "settle_timezone",
    "should_ask_timezone",
    "signal_in",
    "start_followup",
    "start_goal",
    "start_intake",
    "start_objective",
    "start_question",
    "timezone_pending",
]


async def start_question(uid: int, kind: str, text: str) -> str | None:
    """Start the flow for a question chosen by question_due() and return the text to send (None if it turned out not to apply)."""
    if kind == "step_offer":
        return await offer_step(uid, text)
    if kind == "goal":
        return await start_goal(uid)
    if kind == "step":
        return await start_objective(uid)
    return {"followup": start_followup, "intake": start_intake}[kind](uid)


async def answer(uid: int, text: str) -> str | CoachTurn | None:
    """Handle the user's reply to the question in progress. None: it was ordinary chat, let the coach answer."""
    state = get(uid)
    if state is None:
        return None
    said = normalize(text)
    handler = {
        ("goal", "asked"): drafting,
        ("goal", "confirm"): confirming,
        ("goal", "revise"): confirming,
        ("objective", "asked"): drafting,
        ("objective", "confirm"): confirming,
        ("objective", "revise"): confirming,
        ("intake", state["step"]): intake_answer,
        ("objective", "reward"): reward_answer,
        ("followup", "outcome"): outcome_answer,
        ("followup", "barrier"): barrier_answer,
        ("timezone", state["step"]): timezone_answer,
    }.get((state["flow"], state["step"]))
    return await handler(uid, state, text, said) if handler else None
