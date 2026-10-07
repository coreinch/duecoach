"""Code-led coaching flows: set a goal, pick this week's small step (and a reward), report how it went.

The bot asks, the model only helps with wording, and nothing is saved until the user says yes. That keeps the coaching record
reliable: the free models forget to ask, save duplicates and claim to have saved things they haven't.

A flow's state is one small JSON value on the user row (`flow_state`). It expires after a day, so an unanswered question
never blocks anything for long.
"""

import json
import re
import time
from dataclasses import dataclass

from . import db, llm, prompts, strings
from .timezones import normalize

FLOW_TTL = 24 * 3600
MAX_REVISIONS = 3
MAX_VAGUE_ANSWERS = 2
GOAL_MIN_MESSAGES = 2  # stored chat messages (both sides): not before the coach has answered once...
GOAL_FALLBACK_MESSAGES = 6  # ...and if the user never states a problem or wish, ask anyway after about three exchanges
QUESTION_GAP = 4  # stored chat messages that must pass after any question of ours before the next one (two exchanges)
STEP_OFFER_COOLDOWN = 6 * 3600  # after "skip" on a step, don't offer to save one they agreed to for a few hours
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


# --- when is a good moment? ---
#
# The bot adds a question to the coach's reply only at a natural point: the user has just named a problem or a wish, or has agreed
# to try something. It never asks while someone is struggling, straight after a question of its own, or when they asked a question
# themselves. Patterns run on normalize()d text: lower-case, accents and apostrophes removed ("can't" becomes "can t").

PROBLEM_PATTERNS = [
    r"\bi (always|never|keep|constantly|usually|often) \w+",
    r"\bi (can t|cant|cannot|don t|do not|couldn t|won t) (seem to )?(focus|concentrate|start|finish|stop|remember|get|keep|sleep|manage|do|stay)",
    r"\bi (struggle|have trouble|have a hard time|find it hard|m struggling|am struggling|forget|lose|lost)\b",
    r"\bi (am|m|feel|was) (always |so |very |really |constantly )?(late|behind|overwhelmed|stuck|distracted|disorgani[sz]ed|unorgani[sz]ed|exhausted|lost)\b",
    r"\bi (want|need|d like|would like|wish|hope|m trying|am trying|have) to \w+",
    r"\b(ξεχναω|ξεχνω|ξεχασα|χανω|αργω|αναβαλλω|αναβαλω|δυσκολευομαι)\b",
    r"\bδεν (μπορω|καταφερνω|προλαβαινω)\b",
    r"\b(παντα|συνεχεια|διαρκωσ|διαρκως) \w+",
    r"\b(θελω|θα ηθελα|χρειαζομαι|προσπαθω|πρεπει) να\b",
    r"\bμε δυσκολευει\b",
    r"\bνιωθω (χαμεν|μπερδεμεν|κουρασμεν|αγχωμεν|κολλημεν)\w*",
]
STEP_PATTERNS = [
    r"\b(ok|okay|alright|sure|yes|yeah|fine|good|great|cool)\b.{0,20}\b(i ll|i will|i m going to|let s|i can|i could)\b",
    r"\bi (ll|will) (try|put|do|set|start|make|keep|use|write|get|place|leave|lay)\b",
    r"\bi m going to (try|put|do|set|start|make|use|write)\b",
    r"\blet me try\b",
    r"\bsounds (good|great|doable|like a plan)\b",
    r"\bthat (works|could work|might work)\b",
    r"\bθα (το |τα |τον |την )?(δοκιμασω|κανω|βαλω|ξεκινησω|φτιαξω|γραψω|χρησιμοποιησω|κρατησω|αφησω)\b",
    r"\b(ενταξει|οκ|καλα|ναι)\b.{0,20}\bθα\b",
    r"\bακουγεται (καλο|καλα|ωραιο)\b",
]
CRISIS_PATTERNS = [
    r"\b(kill myself|suicid\w*|end(ing)? (it all|my life)|take my own life|want to die|wanna die|hurt(ing)? myself|harm(ing)? myself|self harm|no reason to live)\b",
    r"\b(don t want to (live|be here)|can t go on|better off dead)\b",
    r"\babus(ed|e|ive)\b",
    r"αυτοκτον\w*|να πεθανω|δεν θελω να ζω|τελειωσω (τα παντα|τη ζωη|ολα)|κακο στον εαυτο μου|δεν αντεχω αλλο|κακοποι\w*",
]
# playbook cards that mean the coach is helping with distress rather than with a plan
DISTRESS_CARDS = {"pause_coaching", "anxiety_approach", "self_talk", "setback_reframe", "overload", "too_much_signals"}
_PROBLEM, _STEP, _CRISIS = ([re.compile(p) for p in patterns] for patterns in (PROBLEM_PATTERNS, STEP_PATTERNS, CRISIS_PATTERNS))


def signal_in(text: str) -> str | None:
    """'step' if the user just agreed to try something, 'problem' if they named a struggle or a wish, else None."""
    said = normalize(text)
    if any(p.search(said) for p in _STEP):
        return "step"
    if any(p.search(said) for p in _PROBLEM):
        return "problem"
    return None


def is_crisis(text: str) -> bool:
    said = normalize(text)
    return any(p.search(said) for p in _CRISIS)


def question_blocked(uid: int, text: str) -> bool:
    """True when this is not the moment for a question of the bot's own."""
    if is_crisis(text) or llm.cards_read(uid) & DISTRESS_CARDS:
        return True  # someone in distress needs the coach, not a form
    if text.strip().endswith(("?", ";", "\u037e")):
        return True  # they asked something themselves: answer it and stop
    user = db.get_user(uid)
    return db.message_count(uid) - (user["question_at_count"] if user["question_at_count"] is not None else -100) < QUESTION_GAP


def note_question_asked(uid: int) -> None:
    db.set_field(uid, "question_at_count", db.message_count(uid))


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


async def next_question(uid: int, text: str) -> str | None:
    """Which question (if any) to add to this reply. At most one question is ever pending: callers check get() first."""
    if get(uid):
        return None
    if (question := start_followup(uid)) is not None:
        return question
    user = db.get_user(uid)
    now = time.time()
    goals, opens = db.active_goals(uid), db.open_objectives(uid)
    signal = signal_in(text)
    count = db.message_count(uid)
    if not goals:
        ready = (signal is not None and count >= GOAL_MIN_MESSAGES) or count >= GOAL_FALLBACK_MESSAGES
        if ready and now - (user["goal_asked_at"] or 0) > GOAL_COOLDOWN:
            return start_goal(uid)
    elif not opens:
        if signal == "step" and now - (user["obj_asked_at"] or 0) > STEP_OFFER_COOLDOWN:
            return await offer_step(uid, text)  # they just agreed to something: offer to keep it as this week's step
        if now - (user["obj_asked_at"] or 0) > OBJECTIVE_COOLDOWN:
            return start_objective(uid)
    return None


async def offer_step(uid: int, text: str) -> str | None:
    """The user said what they'll try: draft it as this week's step and ask whether to save it."""
    goal = db.active_goals(uid)[0]
    draft = await llm.draft(uid, "objective", text, goal=goal["text"])
    if draft is None:
        return None
    put(uid, "objective", "confirm", started=time.time(), goal_id=goal["id"], goal=goal["text"], candidate=draft, revisions=0)
    db.set_field(uid, "obj_asked_at", time.time())
    return _t(uid, "OBJ_CONFIRM", step=draft)


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
