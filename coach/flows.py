"""Code-led coaching flows: set a goal, pick this week's small step (and a reward), report how it went.

The bot asks, the model only helps with wording, and nothing is saved until the user says yes. That keeps the coaching record
reliable: the free models forget to ask, save duplicates and claim to have saved things they haven't.

A flow's state is one small JSON value on the user row (`flow_state`). It expires after a day, so an unanswered question
never blocks anything for long.
"""

import json
import time
from dataclasses import dataclass

from . import db, llm, prompts, strings
from .timezones import normalize

FLOW_TTL = 24 * 3600
MAX_REVISIONS = 3
MAX_VAGUE_ANSWERS = 2
GOAL_AFTER_MESSAGES = 4  # stored chat messages (both sides): about two exchanges before the first goal question
GOAL_COOLDOWN = 7 * 86400  # after "skip", wait this long before offering again
OBJECTIVE_COOLDOWN = 2 * 86400
FOLLOWUP_AFTER = 2 * 86400  # a weekly step is asked about once it is this old...
FOLLOWUP_COOLDOWN = 20 * 3600  # ...and then at most once per ~day

YES = {"yes", "y", "yeah", "yep", "yup", "correct", "right", "ok", "okay", "sure", "ναι", "ν", "σωστα", "σωστο", "ενταξει", "οκ"}
NO = {"no", "n", "nope", "wrong", "incorrect", "no thanks", "οχι", "λαθος", "οχι ευχαριστω"}
SKIP = {"skip", "later", "pass", "not now", "skip it", "παραληψη", "αργοτερα", "οχι τωρα", "δεν θελω"}
NONE_WORDS = {"none", "no", "nothing", "skip", "no reward", "κανενα", "τιποτα", "οχι", "παραληψη", "χωρις"}
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


@dataclass
class CoachTurn:
    """The flow is over and the coach should now respond to what the user just reported."""

    text: str
    instruction: str


def _t(uid: int, key: str, **kw) -> str:
    return strings.t(db.get_user(uid)["lang"], key, **kw)


# --- state ---


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


def _word_count(text: str) -> int:
    return len(text.split())


# --- starting a flow (each returns the question to send) ---


def start_goal(uid: int) -> str:
    put(uid, "goal", "asked", started=time.time(), vague=0)
    db.set_field(uid, "goal_asked_at", time.time())
    return _t(uid, "GOAL_ASK")


def start_objective(uid: int) -> str:
    """Ask for this week's step; goes to the goal question first if there is no goal yet."""
    goals = db.active_goals(uid)
    if not goals:
        return start_goal(uid)
    if len(db.open_objectives(uid)) >= db.MAX_OPEN_OBJECTIVES:
        return _t(uid, "OBJ_FULL")
    put(uid, "objective", "asked", started=time.time(), goal_id=goals[0]["id"], goal=goals[0]["text"], vague=0)
    db.set_field(uid, "obj_asked_at", time.time())
    return _t(uid, "OBJ_ASK", goal=goals[0]["text"])


def _due_followup(uid: int):
    """The oldest open weekly step that is old enough to ask about, unless we asked recently."""
    user = db.get_user(uid)
    now = time.time()
    if now - (user["followup_asked_at"] or 0) < FOLLOWUP_COOLDOWN:
        return None
    return next((o for o in db.open_objectives(uid) if now - o["created"] >= FOLLOWUP_AFTER), None)


def start_followup(uid: int) -> str | None:
    objective = _due_followup(uid)
    if objective is None or get(uid):
        return None
    put(uid, "followup", "outcome", started=time.time(), objective_id=objective["id"], step_label=objective["text"], vague=0)
    db.set_field(uid, "followup_asked_at", time.time())
    return _t(uid, "FU_ASK", step=objective["text"])


def next_question(uid: int) -> str | None:
    """Which question (if any) to add to this reply. At most one question is ever pending: callers check get() first."""
    if get(uid):
        return None
    if (question := start_followup(uid)) is not None:
        return question
    user = db.get_user(uid)
    now = time.time()
    goals, opens = db.active_goals(uid), db.open_objectives(uid)
    if not goals:
        if db.message_count(uid) >= GOAL_AFTER_MESSAGES and now - (user["goal_asked_at"] or 0) > GOAL_COOLDOWN:
            return start_goal(uid)
    elif not opens and now - (user["obj_asked_at"] or 0) > OBJECTIVE_COOLDOWN:
        return start_objective(uid)
    return None


# --- answering the pending question ---


async def answer(uid: int, text: str) -> str | CoachTurn | None:
    """Handle the user's reply to the question in progress. None: it was ordinary chat, let the coach answer."""
    state = get(uid)
    if state is None:
        return None
    said = normalize(text)
    handler = {
        ("goal", "asked"): _drafting,
        ("goal", "confirm"): _confirming,
        ("goal", "revise"): _confirming,
        ("objective", "asked"): _drafting,
        ("objective", "confirm"): _confirming,
        ("objective", "revise"): _confirming,
        ("objective", "reward"): _reward,
        ("followup", "outcome"): _outcome,
        ("followup", "barrier"): _barrier,
    }.get((state["flow"], state["step"]))
    return await handler(uid, state, text, said) if handler else None


def _decline(uid: int, state: dict) -> str:
    """The user said skip: stop, and don't offer this again for a while."""
    clear(uid)
    column = {"goal": "goal_asked_at", "objective": "obj_asked_at", "followup": "followup_asked_at"}[state["flow"]]
    db.set_field(uid, column, time.time())
    return _t(uid, {"goal": "GOAL_SKIPPED", "objective": "OBJ_SKIPPED", "followup": "FU_LATER"}[state["flow"]])


async def _drafting(uid: int, state: dict, text: str, said: str):
    """First answer to 'what is your goal / this week's step?': draft the wording and ask for a yes."""
    if said in SKIP or said in NO:
        return _decline(uid, state)
    kind = state["flow"]
    draft = await llm.draft(uid, kind, text, goal=state.get("goal", "")) if _word_count(text) >= 2 else None
    if draft is None:
        vague = state.get("vague", 0) + 1  # a greeting, thanks, or one word: ask for more once, then treat it as ordinary chat
        if vague > MAX_VAGUE_ANSWERS:
            clear(uid)
            return None
        put(uid, kind, "asked", **{k: v for k, v in state.items() if k not in ("flow", "step", "started", "vague")}, vague=vague)
        return _t(uid, "GOAL_MORE" if kind == "goal" else "OBJ_MORE")
    _ask_to_confirm(uid, state, draft, revisions=0)
    return _t(uid, "GOAL_CONFIRM" if kind == "goal" else "OBJ_CONFIRM", goal=draft, step=draft)


def _ask_to_confirm(uid: int, state: dict, candidate: str, revisions: int) -> None:
    keep = {k: v for k, v in state.items() if k not in ("flow", "step", "started", "candidate", "revisions", "vague")}
    put(uid, state["flow"], "confirm", candidate=candidate, revisions=revisions, **keep)


async def _confirming(uid: int, state: dict, text: str, said: str):
    kind, candidate = state["flow"], state["candidate"]
    if said in SKIP:
        return _decline(uid, state)
    if said in YES:
        return _accept(uid, state)
    if said in NO and state["step"] == "confirm":
        put(uid, kind, "revise", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
        return _t(uid, "REVISE")
    revisions = state.get("revisions", 0) + 1
    if revisions > MAX_REVISIONS:  # enough rounds: take the current draft, it can be changed later
        return _accept(uid, state)
    revised = await llm.draft(uid, kind, text, previous=candidate, goal=state.get("goal", ""))
    _ask_to_confirm(uid, state, revised or candidate, revisions)
    return _t(uid, "GOAL_CONFIRM" if kind == "goal" else "OBJ_CONFIRM", goal=revised or candidate, step=revised or candidate)


def _accept(uid: int, state: dict) -> str:
    """The user agreed to the wording: a goal is saved now, a weekly step first asks for a reward."""
    candidate = state["candidate"]
    if state["flow"] == "goal":
        clear(uid)
        already = candidate.casefold() in {g["text"].casefold() for g in db.active_goals(uid)}
        if not already and db.add_goal(uid, candidate) is None:
            return _t(uid, "GOAL_FULL")
        return f"{_t(uid, 'GOAL_SAVED', goal=candidate)}\n\n{start_objective(uid)}"  # the natural next question: a first small step
    put(uid, "objective", "reward", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
    return _t(uid, "OBJ_REWARD")


async def _reward(uid: int, state: dict, text: str, said: str):
    reward = "" if said in NONE_WORDS else " ".join(text.split())[:100]
    saved = db.add_objective(uid, state.get("goal_id"), state["candidate"], reward)
    clear(uid)
    if saved is None:
        return _t(uid, "OBJ_FULL")
    return _t(uid, "OBJ_SAVED", step=state["candidate"], reward=_t(uid, "OBJ_REWARD_NOTE", reward=reward) if reward else "")


def _parse_outcome(said: str) -> str | None:
    return next((outcome for outcome, words in OUTCOMES.items() if said in words), None)


async def _outcome(uid: int, state: dict, text: str, said: str):
    if said in SKIP:
        return _decline(uid, state)
    outcome = _parse_outcome(said)
    if outcome is None:
        vague = state.get("vague", 0) + 1
        if vague > MAX_VAGUE_ANSWERS:
            clear(uid)
            return None
        put(uid, "followup", "outcome", **{k: v for k, v in state.items() if k not in ("flow", "step", "started", "vague")}, vague=vague)
        return _t(uid, "FU_AGAIN")
    return await _after_outcome(uid, state, outcome)


async def _after_outcome(uid: int, state: dict, outcome: str):
    objective_step = state["step_label"]
    if outcome == "done":
        db.close_objective(uid, state["objective_id"], "done", "", "")
        clear(uid)
        return CoachTurn("(step result) done", prompts.FOLLOWUP_DONE.format(step=objective_step))
    put(uid, "followup", "barrier", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")}, outcome=outcome)
    return _t(uid, "FU_BARRIER")


async def _barrier(uid: int, state: dict, text: str, said: str):
    if said in SKIP:
        return _decline(uid, state)
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
