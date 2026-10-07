"""Asking how the weekly step went, and what got in the way."""

import time

from .. import db, prompts
from .goals import MAX_VAGUE_ANSWERS, decline
from .moments import due_followup
from .state import SKIP, CoachTurn, clear, get, put, say

OUTCOMES = {
    "done": {"1", "done", "did it", "finished", "yes", "ναι", "εγινε", "το εκανα", "τελειωσα"},
    "partial": {"2", "partly", "partially", "some", "a bit", "kind of", "κατα μερος", "εν μερει", "λιγο", "κατι"},
    "missed": {"3", "not yet", "no", "missed", "didn t", "did not", "not", "οχι", "οχι ακομα", "δεν το εκανα", "δεν εγινε"},
}


BARRIERS = {"1": "forgot", "2": "didnt_know_how", "3": "confused", "4": "avoidance", "5": "low_motivation"}


BARRIER_LABELS = {
    "forgot": "forgot",
    "didnt_know_how": "didn't know how",
    "confused": "got confused about it",
    "avoidance": "avoiding it",
    "low_motivation": "low motivation",
    "other": "something else",
}


def start_followup(uid: int) -> str | None:
    objective = due_followup(uid)
    if objective is None or get(uid):
        return None
    put(uid, "followup", "outcome", started=time.time(), objective_id=objective["id"], step_label=objective["text"], vague=0)
    db.set_field(uid, "followup_asked_at", time.time())
    return say(uid, "FU_ASK", step=objective["text"])


def _parse_outcome(said: str) -> str | None:
    return next((outcome for outcome, words in OUTCOMES.items() if said in words), None)


async def outcome_answer(uid: int, state: dict, text: str, said: str):
    if said in SKIP:
        return decline(uid, state)
    outcome = _parse_outcome(said)
    if outcome is None:
        vague = state.get("vague", 0) + 1
        if vague > MAX_VAGUE_ANSWERS:
            clear(uid)
            return None
        put(uid, "followup", "outcome", **{k: v for k, v in state.items() if k not in ("flow", "step", "started", "vague")}, vague=vague)
        return say(uid, "FU_AGAIN")
    return await _after_outcome(uid, state, outcome)


async def _after_outcome(uid: int, state: dict, outcome: str):
    objective_step = state["step_label"]
    if outcome == "done":
        db.close_objective(uid, state["objective_id"], "done", "", "")
        clear(uid)
        return CoachTurn("(step result) done", prompts.FOLLOWUP_DONE.format(step=objective_step))
    put(uid, "followup", "barrier", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")}, outcome=outcome)
    return say(uid, "FU_BARRIER")


async def barrier_answer(uid: int, state: dict, text: str, said: str):
    if said in SKIP:
        return decline(uid, state)
    code = BARRIERS.get(said)
    barrier, note = (code, "") if code else ("other", " ".join(text.split())[:200])
    outcome = state["outcome"]
    db.close_objective(uid, state["objective_id"], outcome, barrier, note)
    clear(uid)
    detail = f" (their words: {note})" if note else ""
    instruction = prompts.FOLLOWUP_BARRIER.format(
        outcome="partly done" if outcome == "partial" else "not done",
        step=state["step_label"],
        barrier=BARRIER_LABELS[barrier],
        note=detail,
    )
    return CoachTurn(f"(step result) {outcome}, barrier: {BARRIER_LABELS[barrier]}", instruction)
