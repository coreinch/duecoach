"""Setting a goal and a weekly step: draft with the model, confirm with the user, then save."""

import time

from .. import db, llm
from .goal_ideas import goal_options, picked_option
from .onboarding import with_next_setup_step
from .state import NO, NONE_WORDS, SKIP, YES, clear, put, say, word_count

MAX_REVISIONS = 3


MAX_VAGUE_ANSWERS = 2


async def start_goal(uid: int, after_intake: bool = False, options: list[str] | None = None) -> str:
    """Ask for a goal by offering three ideas to pick from (or the user's own), or openly if there are no ideas. Right after the
    interview the question picks up what they said got in their way. `options` can be prepared ahead (while the coach writes)."""
    options = options or await goal_options(uid)
    put(uid, "goal", "asked", started=time.time(), vague=0, options=options)
    db.set_field(uid, "goal_asked_at", time.time())
    obstacle = db.get_profile(uid).get("obstacle", "") if after_intake else ""
    quoted = obstacle[:100].rstrip(" .,;")
    if not options:  # no ideas to offer: an open question
        return say(uid, "GOAL_ASK_OPEN_INTAKE", obstacle=quoted) if obstacle else say(uid, "GOAL_ASK_OPEN")
    a, b, c = options
    return say(uid, "GOAL_ASK_INTAKE", obstacle=quoted, a=a, b=b, c=c) if obstacle else say(uid, "GOAL_ASK", a=a, b=b, c=c)


async def start_objective(uid: int) -> str:
    """Ask for this week's step; goes to the goal question first if there is no goal yet."""
    goals = db.active_goals(uid)
    if not goals:
        return await start_goal(uid)
    if len(db.open_objectives(uid)) >= db.MAX_OPEN_OBJECTIVES:
        return say(uid, "OBJ_FULL")
    put(uid, "objective", "asked", started=time.time(), goal_id=goals[0]["id"], goal=goals[0]["text"], vague=0)
    db.set_field(uid, "obj_asked_at", time.time())
    goal_text = goals[0]["text"].strip().rstrip(".")
    # a short goal is quoted; a long one was just shown, so repeating it would only be clumsy
    return say(uid, "OBJ_ASK", goal=f"\u201c{goal_text}\u201d" if len(goal_text) <= 60 else say(uid, "THAT_GOAL"))


async def offer_step(uid: int, text: str) -> str | None:
    """The user said what they'll try: draft it as this week's step and ask whether to save it."""
    goal = db.active_goals(uid)[0]
    draft = await llm.draft(uid, "objective", text, goal=goal["text"])
    if draft is None:
        return None
    put(uid, "objective", "confirm", started=time.time(), goal_id=goal["id"], goal=goal["text"], candidate=draft, revisions=0)
    db.set_field(uid, "obj_asked_at", time.time())
    return say(uid, "OBJ_CONFIRM", step=draft)


def decline(uid: int, state: dict) -> str:
    """The user said skip: stop, and don't offer this again for a while."""
    clear(uid)
    column = {"goal": "goal_asked_at", "objective": "obj_asked_at", "followup": "followup_asked_at"}[state["flow"]]
    db.set_field(uid, column, time.time())
    reply = say(uid, {"goal": "GOAL_SKIPPED", "objective": "OBJ_SKIPPED", "followup": "FU_LATER"}[state["flow"]])
    return with_next_setup_step(uid, reply) if state["flow"] != "followup" else reply


async def drafting(uid: int, state: dict, text: str, said: str):
    """First answer to 'what is your goal / this week's step?': draft the wording and ask for a yes."""
    if said in SKIP or said in NO:
        return decline(uid, state)
    kind = state["flow"]
    if kind == "goal" and state.get("options") and (pick := picked_option(text, len(state["options"]))):
        index, rest = pick
        chosen = state["options"][index]
        if not rest:  # choosing one of the offered ideas is a yes: save it now
            return await _accept(uid, {**state, "candidate": chosen})
        revised = await llm.draft(uid, "goal", rest, previous=chosen)  # "2, but only on weekdays": their change applied to that idea
        _ask_to_confirm(uid, state, revised or chosen, revisions=1)
        return say(uid, "GOAL_CONFIRM", goal=revised or chosen)
    draft = await llm.draft(uid, kind, text, goal=state.get("goal", "")) if word_count(text) >= 2 else None
    if draft is None:
        vague = state.get("vague", 0) + 1  # a greeting, thanks, or one word: ask for more once, then treat it as ordinary chat
        if vague > MAX_VAGUE_ANSWERS:
            clear(uid)
            return None
        put(uid, kind, "asked", **{k: v for k, v in state.items() if k not in ("flow", "step", "started", "vague")}, vague=vague)
        return say(uid, "GOAL_MORE" if kind == "goal" else "OBJ_MORE")
    _ask_to_confirm(uid, state, draft, revisions=0)
    return say(uid, "GOAL_CONFIRM" if kind == "goal" else "OBJ_CONFIRM", goal=draft, step=draft)


def _ask_to_confirm(uid: int, state: dict, candidate: str, revisions: int) -> None:
    keep = {k: v for k, v in state.items() if k not in ("flow", "step", "started", "candidate", "revisions", "vague")}
    put(uid, state["flow"], "confirm", candidate=candidate, revisions=revisions, **keep)


async def confirming(uid: int, state: dict, text: str, said: str):
    kind, candidate = state["flow"], state["candidate"]
    if said in SKIP:
        return decline(uid, state)
    if said in YES:
        return await _accept(uid, state)
    if said in NO and state["step"] == "confirm":
        put(uid, kind, "revise", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
        return say(uid, "REVISE")
    revisions = state.get("revisions", 0) + 1
    if revisions > MAX_REVISIONS:  # enough rounds: take the current draft, it can be changed later
        return await _accept(uid, state)
    revised = await llm.draft(uid, kind, text, previous=candidate, goal=state.get("goal", ""))
    _ask_to_confirm(uid, state, revised or candidate, revisions)
    return say(uid, "GOAL_CONFIRM" if kind == "goal" else "OBJ_CONFIRM", goal=revised or candidate, step=revised or candidate)


async def _accept(uid: int, state: dict) -> str:
    """The user agreed to the wording: a goal is saved now, a weekly step first asks for a reward."""
    candidate = state["candidate"]
    if state["flow"] == "goal":
        clear(uid)
        already = candidate.casefold() in {g["text"].casefold() for g in db.active_goals(uid)}
        if not already and db.add_goal(uid, candidate) is None:
            return say(uid, "GOAL_FULL")
        return f"{say(uid, 'GOAL_SAVED', goal=candidate)}\n\n{await start_objective(uid)}"  # the natural next question: a first small step
    put(uid, "objective", "reward", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
    return say(uid, "OBJ_REWARD")


async def reward_answer(uid: int, state: dict, text: str, said: str):
    reward = "" if said in NONE_WORDS else " ".join(text.split())[:100]
    saved = db.add_objective(uid, state.get("goal_id"), state["candidate"], reward)
    clear(uid)
    if saved is None:
        return say(uid, "OBJ_FULL")
    saved_text = say(uid, "OBJ_SAVED", step=state["candidate"], reward=say(uid, "OBJ_REWARD_NOTE", reward=reward) if reward else "")
    return with_next_setup_step(uid, saved_text)
