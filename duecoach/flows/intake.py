"""The intake interview: a few questions about why they came, what they tried, what gets in the way."""

import asyncio
import re
import time

from .. import db, prompts
from .goal_ideas import goal_options, prefetch_goal_options
from .goals import start_goal
from .state import SKIP, STOP, CoachTurn, clear, put, say

INTAKE_STEPS = ["why", "tried", "obstacle", "strength", "rhythm", "mood"]


INTAKE_MAX_STARTS = 3  # ...but only this many times, then it is dropped (the user can still run /intake)


INTAKE_ANSWER_LIMIT = 300  # characters kept per answer


HEAVY_MOOD_PATTERNS = [
    r"depress\w*|hopeless|burn(ed|t)? out|burnout|panic\w*|anxi\w*|can t cope|falling apart|really low|very low|so sad|cry(ing)? a lot",
    r"καταθλιψ\w*|αγχ\w*|πανικ\w*|χαλια|πολυ χαμηλα|κλαιω|ψυχολογια μου",
]


_HEAVY_MOOD = [re.compile(p) for p in HEAVY_MOOD_PATTERNS]


def start_intake(uid: int, restart: bool = False) -> str | None:
    """Begin (or resume) the intake interview at the first topic not yet answered; /intake with restart=True asks them all again."""
    profile = {} if restart else db.get_profile(uid)
    starts = profile.get("_starts", 0) + 1
    if starts > INTAKE_MAX_STARTS and not restart:  # they keep leaving it unfinished: stop offering
        db.set_field(uid, "intake_state", "skipped")
        return None
    profile["_starts"] = starts
    db.set_profile(uid, profile)
    step = next((s for s in INTAKE_STEPS if s not in profile), INTAKE_STEPS[0])
    put(uid, "intake", step, started=time.time())
    db.set_fields(uid, intake_asked_at=time.time(), intake_state="")
    intro = say(uid, "INTAKE_INTRO") + "\n\n" if step == INTAKE_STEPS[0] else ""
    return intro + say(uid, f"INTAKE_Q_{step}")


async def intake_answer(uid: int, state: dict, text: str, said: str):
    """One answer of the interview: keep it as given, then ask the next topic. 'skip' passes on a topic, 'stop' ends the interview."""
    step = state["step"]
    profile = db.get_profile(uid)
    if said in STOP:
        return _finish_intake(uid, profile, stopped=True)
    note = ""
    if said not in SKIP:
        profile[step] = " ".join(text.split())[:INTAKE_ANSWER_LIMIT]
        if step == "mood" and any(p.search(said) for p in _HEAVY_MOOD):
            profile["mood_heavy"] = True
            note = "\n\n" + say(uid, "INTAKE_MOOD_HEAVY")
        db.set_profile(uid, profile)
        if step == "obstacle":
            prefetch_goal_options(uid)  # the later answers give the model time to think the ideas through
    following = INTAKE_STEPS[INTAKE_STEPS.index(step) + 1 :]
    if not following:
        closing = _finish_intake(uid, profile)
        if note or db.active_goals(uid):
            return closing + note  # after a heavy answer, or when redoing the interview with goals already set: nothing more is pushed
        ideas = asyncio.create_task(goal_options(uid))  # starts now, so it runs while the coach writes its reply

        async def goal_question() -> str:
            return await start_goal(uid, after_intake=True, options=await ideas)

        # carry on straight away: the coach says what it understood and how this works, then the first goal question follows
        return CoachTurn("(finished the intake interview)", prompts.INTAKE_WRAPUP, follow_up=goal_question, fallback=closing)
    put(uid, "intake", following[0])
    return f"{say(uid, 'INTAKE_ACK')} {say(uid, f'INTAKE_Q_{following[0]}')}"


def _finish_intake(uid: int, profile: dict, stopped: bool = False) -> str:
    clear(uid)
    answered = any(topic in profile for topic in INTAKE_STEPS)
    # the interview answers are not stored chat messages, so they don't advance the "no questions back to back" count: release it
    db.set_fields(uid, intake_state="done" if answered else "skipped", question_at_count=-100)
    return say(uid, "INTAKE_STOPPED" if stopped else "INTAKE_DONE")
