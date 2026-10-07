"""Code-led coaching flows: set a goal, pick this week's small step (and a reward), report how it went.

The bot asks, the model only helps with wording, and nothing is saved until the user says yes. That keeps the coaching record
reliable: the free models forget to ask, save duplicates and claim to have saved things they haven't.

A flow's state is one small JSON value on the user row (`flow_state`). It expires after a day, so an unanswered question
never blocks anything for long.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass

from . import db, llm, prompts, strings
from .timezones import normalize

FLOW_TTL = 24 * 3600
MAX_REVISIONS = 3
MAX_VAGUE_ANSWERS = 2
GOAL_MIN_MESSAGES = 2  # stored chat messages (both sides) before this exchange: the coach has answered once already...
GOAL_FALLBACK_MESSAGES = 6  # ...and if the user never states a problem or wish, ask anyway after about three exchanges
QUESTION_GAP = 4  # stored chat messages that must pass after any question of ours before the next one (two exchanges)
STEP_OFFER_COOLDOWN = 6 * 3600  # after "skip" on a step, don't offer to save one they agreed to for a few hours
GOAL_COOLDOWN = 7 * 86400  # after "skip", wait this long before offering again
OBJECTIVE_COOLDOWN = 2 * 86400
FOLLOWUP_AFTER = 2 * 86400  # a weekly step is asked about once it is this old...
FOLLOWUP_COOLDOWN = 20 * 3600  # ...and then at most once per ~day

INTAKE_STEPS = ["why", "tried", "obstacle", "strength", "rhythm", "mood"]
INTAKE_MIN_MESSAGES = 2  # stored chat messages before this exchange: the coach has answered once before the interview starts
INTAKE_COOLDOWN = 2 * 86400  # an interview that was left unfinished is offered again after this long...
INTAKE_MAX_STARTS = 3  # ...but only this many times, then it is dropped (the user can still run /intake)
INTAKE_ANSWER_LIMIT = 300  # characters kept per answer
STOP = {"stop", "enough", "no more", "that s enough", "that s all", "αρκετα", "σταματα", "τελος", "οχι αλλο", "δεν θελω αλλες"}
HEAVY_MOOD_PATTERNS = [
    r"depress\w*|hopeless|burn(ed|t)? out|burnout|panic\w*|anxi\w*|can t cope|falling apart|really low|very low|so sad|cry(ing)? a lot",
    r"καταθλιψ\w*|αγχ\w*|πανικ\w*|χαλια|πολυ χαμηλα|κλαιω|ψυχολογια μου",
]
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


SUGGEST_TIMEOUT = 20  # seconds to wait for the model's goal ideas before using the built-in ones
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

# Built-in goal ideas, used when the model can't suggest any. Each is measurable, says how and has a time frame. Keywords are
# matched against the user's interview answers and recent messages after normalize() (so no accents).
GOAL_IDEAS = [
    (
        r"start|begin|procrastin|put off|ξεκιν|αναβαλ",
        "Start my most important task within 10 minutes of sitting down to work, on at least 4 days a week for the next month.",
        "Να ξεκινάω την πιο σημαντική εργασία μου μέσα σε 10 λεπτά από τη στιγμή που κάθομαι να δουλέψω, τουλάχιστον 4 μέρες την εβδομάδα για τον επόμενο μήνα.",
    ),
    (
        r"time|late|punctual|deadline|rush|χρον|αργ|ωρα|προθεσμ",
        "Leave the house 10 minutes earlier than I need to, on at least 4 days a week for the next month.",
        "Να φεύγω από το σπίτι 10 λεπτά νωρίτερα από όσο χρειάζεται, τουλάχιστον 4 μέρες την εβδομάδα για τον επόμενο μήνα.",
    ),
    (
        r"organi[sz]|keys|lose|lost|forget|remember|clutter|mess|οργαν|κλειδ|χανω|χαν|ξεχν|θυμ|ατακτ",
        "Put my keys, phone and wallet in one fixed place every time I come home, every day for the next four weeks.",
        "Να βάζω κλειδιά, κινητό και πορτοφόλι πάντα στο ίδιο σημείο όταν γυρνάω σπίτι, κάθε μέρα για τις επόμενες τέσσερις εβδομάδες.",
    ),
    (
        r"focus|distract|concentrat|attention|phone|συγκεντρ|αποσπ|προσοχ|κινητο",
        "Work in one focused 25-minute block with my phone out of reach, on at least 4 days a week for the next month.",
        "Να δουλεύω σε ένα συγκεντρωμένο μπλοκ 25 λεπτών με το κινητό μακριά μου, τουλάχιστον 4 μέρες την εβδομάδα για τον επόμενο μήνα.",
    ),
    (
        r"sleep|bed|tired|exhaust|insomnia|υπν|κοιμ|κουρασ",
        "Start winding down at a fixed time and be in bed by it on at least 5 nights a week for the next month.",
        "Να αρχίζω να ηρεμώ σε σταθερή ώρα και να είμαι στο κρεβάτι μέχρι τότε τουλάχιστον 5 βράδια την εβδομάδα για τον επόμενο μήνα.",
    ),
    (
        r"stress|anxi|overwhelm|pressure|burn|αγχ|πιεσ|εξαντλ",
        "Take a 10-minute break to reset every weekday afternoon for the next four weeks.",
        "Να κάνω ένα διάλειμμα 10 λεπτών για να ηρεμήσω κάθε απόγευμα των καθημερινών για τις επόμενες τέσσερις εβδομάδες.",
    ),
    (
        r"plan|priorit|to do|todo|list|schedule|routine|προγραμμα|λιστα|προτεραι|ρουτινα",
        "Write my top three things for the day each morning and tick them off, on at least 5 days a week for the next month.",
        "Να γράφω κάθε πρωί τα τρία σημαντικότερα της ημέρας και να τα τσεκάρω, τουλάχιστον 5 μέρες την εβδομάδα για τον επόμενο μήνα.",
    ),
]
DEFAULT_IDEAS = (0, 1, 2)  # indexes into GOAL_IDEAS used to fill up to three when the text matched fewer


def fallback_goal_options(uid: int) -> list[str]:
    """Three built-in goal ideas, the ones whose keywords best match what the user has said (generic ones fill the gaps)."""
    profile = db.get_profile(uid)
    said = " ".join(m["content"] for m in db.recent_messages(uid, 12) if m["role"] == "user")
    text = normalize(" ".join([profile.get("obstacle", ""), profile.get("why", ""), profile.get("tried", ""), said]))
    scored = sorted(((len(re.findall(pattern, text)), -index, index) for index, (pattern, _, _) in enumerate(GOAL_IDEAS)), reverse=True)
    chosen = [index for score, _, index in scored if score > 0][:3]
    chosen += [i for i in DEFAULT_IDEAS if i not in chosen][: 3 - len(chosen)]
    greek = db.get_user(uid)["lang"] == "el"
    return [GOAL_IDEAS[i][2 if greek else 1] for i in chosen]


async def goal_options(uid: int) -> list[str]:
    """Three goal ideas: the model's, drawn from what the user said, or the built-in ones if it is slow, down or unsure."""
    try:
        options = await asyncio.wait_for(llm.suggest_goals(uid), SUGGEST_TIMEOUT)
    except Exception:
        options = []
    return options if len(options) == 3 else fallback_goal_options(uid)


def _picked_option(text: str, count: int) -> tuple[int, str] | None:
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


@dataclass
class CoachTurn:
    """The flow is over and the coach should now respond to what the user just reported."""

    text: str
    instruction: str
    follow_up: str = ""  # a question of the bot's to add after the coach's reply
    fallback: str = ""  # what to send instead if the model is unavailable


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


_HEAVY_MOOD = [re.compile(p) for p in HEAVY_MOOD_PATTERNS]


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
    """True when this is not the moment for a question of the bot's own (known before the coach writes its reply)."""
    if is_crisis(text):
        return True  # someone in distress needs the coach, not a form
    if text.strip().endswith(("?", ";", "\u037e")):
        return True  # they asked something themselves: answer it and stop
    user = db.get_user(uid)
    return db.message_count(uid) - (user["question_at_count"] if user["question_at_count"] is not None else -100) < QUESTION_GAP


def distress_in_reply(uid: int) -> bool:
    """The coach just read a card about distress, which only shows once the reply is written: drop the question then."""
    return bool(llm.cards_read(uid) & DISTRESS_CARDS)


def note_question_asked(uid: int) -> None:
    db.set_field(uid, "question_at_count", db.message_count(uid))


# --- starting a flow (each returns the question to send) ---


# --- the setup conversation: intro -> interview -> goal -> first step -> timezone -> hand-over to normal coaching ---
#
# Each stage ends by leading into the next one in the same message, so the user is never left wondering what to do. The hand-over
# happens once, when the setup is over, and says what happens from here and what to do first.


def mark_onboarded(uid: int) -> None:
    db.set_field(uid, "onboarded", 1)


def handoff(uid: int) -> str:
    """The last message of the setup (shown once): how to use the bot from here, and the first thing to do with the step."""
    user = db.get_user(uid)
    if user["onboarded"]:
        return ""
    mark_onboarded(uid)
    goals, opens = db.active_goals(uid), db.open_objectives(uid)
    parts = [_t(uid, "HANDOFF")]
    if opens:
        parts.append(_t(uid, "HANDOFF_STEP", step=opens[0]["text"]))
    elif goals:
        parts.append(_t(uid, "HANDOFF_NO_STEP"))
    else:
        parts.append(_t(uid, "HANDOFF_NO_GOAL"))
    if not user["tz_set"]:
        parts.append(_t(uid, "HANDOFF_NO_TZ"))
    return " ".join(parts)


def onboarding_next(uid: int) -> str:
    """What follows a finished setup stage: the timezone question if it is still open, otherwise the hand-over; "" once all done."""
    user = db.get_user(uid)
    if user["onboarded"] or user["tz_state"] in ("asked", "verify", "time"):
        return ""
    if not user["tz_set"] and not user["tz_state"]:
        db.set_fields(uid, tz_state="asked", tz_attempts=0)
        note_question_asked(uid)
        return _t(uid, "TZ_ASK_LAST")
    return handoff(uid)


def _with_next_setup_step(uid: int, reply: str) -> str:
    following = onboarding_next(uid)
    return f"{reply}\n\n{following}" if following else reply


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
    intro = _t(uid, "INTAKE_INTRO") + "\n\n" if step == INTAKE_STEPS[0] else ""
    return intro + _t(uid, f"INTAKE_Q_{step}")


async def start_goal(uid: int, after_intake: bool = False) -> str:
    """Ask for a goal by offering three ideas to pick from (or the user's own). Right after the interview the question picks up
    what they said got in their way."""
    options = await goal_options(uid)
    put(uid, "goal", "asked", started=time.time(), vague=0, options=options)
    db.set_field(uid, "goal_asked_at", time.time())
    a, b, c = options
    obstacle = db.get_profile(uid).get("obstacle", "") if after_intake else ""
    if obstacle:
        return _t(uid, "GOAL_ASK_INTAKE", obstacle=obstacle[:100].rstrip(" .,;"), a=a, b=b, c=c)
    return _t(uid, "GOAL_ASK", a=a, b=b, c=c)


async def start_objective(uid: int) -> str:
    """Ask for this week's step; goes to the goal question first if there is no goal yet."""
    goals = db.active_goals(uid)
    if not goals:
        return await start_goal(uid)
    if len(db.open_objectives(uid)) >= db.MAX_OPEN_OBJECTIVES:
        return _t(uid, "OBJ_FULL")
    put(uid, "objective", "asked", started=time.time(), goal_id=goals[0]["id"], goal=goals[0]["text"], vague=0)
    db.set_field(uid, "obj_asked_at", time.time())
    goal_text = goals[0]["text"].strip().rstrip(".")
    # a short goal is quoted; a long one was just shown, so repeating it would only be clumsy
    return _t(uid, "OBJ_ASK", goal=f"\u201c{goal_text}\u201d" if len(goal_text) <= 60 else _t(uid, "THAT_GOAL"))


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


def question_due(uid: int, text: str) -> str | None:
    """Which question would suit this moment: 'followup', 'goal', 'step_offer', 'step', or None. Changes nothing."""
    if get(uid):
        return None
    if _due_followup(uid) is not None:
        return "followup"
    user = db.get_user(uid)
    now = time.time()
    goals, opens = db.active_goals(uid), db.open_objectives(uid)
    signal = signal_in(text)
    prior = db.message_count(uid)  # messages stored before this exchange; it will add the user's message and the coach's reply
    if user["intake_state"] == "":  # the interview comes first; goals are set once it is done or skipped
        return "intake" if prior >= INTAKE_MIN_MESSAGES and now - (user["intake_asked_at"] or 0) > INTAKE_COOLDOWN else None
    if not goals:
        ready = (signal is not None and prior >= GOAL_MIN_MESSAGES) or prior + 2 >= GOAL_FALLBACK_MESSAGES
        return "goal" if ready and now - (user["goal_asked_at"] or 0) > GOAL_COOLDOWN else None
    if opens:
        return None
    if signal == "step" and now - (user["obj_asked_at"] or 0) > STEP_OFFER_COOLDOWN:
        return "step_offer"  # they just agreed to something: offer to keep it as this week's step
    return "step" if now - (user["obj_asked_at"] or 0) > OBJECTIVE_COOLDOWN else None


async def start_question(uid: int, kind: str, text: str) -> str | None:
    """Start the flow for a question chosen by question_due() and return the text to send (None if it turned out not to apply)."""
    if kind == "step_offer":
        return await offer_step(uid, text)
    if kind == "goal":
        return await start_goal(uid)
    if kind == "step":
        return await start_objective(uid)
    return {"followup": start_followup, "intake": start_intake}[kind](uid)


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
        ("intake", state["step"]): _intake_answer,
        ("objective", "reward"): _reward,
        ("followup", "outcome"): _outcome,
        ("followup", "barrier"): _barrier,
    }.get((state["flow"], state["step"]))
    return await handler(uid, state, text, said) if handler else None


async def _intake_answer(uid: int, state: dict, text: str, said: str):
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
            note = "\n\n" + _t(uid, "INTAKE_MOOD_HEAVY")
        db.set_profile(uid, profile)
    following = INTAKE_STEPS[INTAKE_STEPS.index(step) + 1 :]
    if not following:
        closing = _finish_intake(uid, profile)
        if note or db.active_goals(uid):
            return closing + note  # after a heavy answer, or when redoing the interview with goals already set: nothing more is pushed
        question = await start_goal(uid, after_intake=True)
        # carry on straight away: the coach says what it understood and how this works, then the first goal question follows
        return CoachTurn("(finished the intake interview)", prompts.INTAKE_WRAPUP, follow_up=question, fallback=f"{closing}\n\n{question}")
    put(uid, "intake", following[0])
    return f"{_t(uid, 'INTAKE_ACK')} {_t(uid, f'INTAKE_Q_{following[0]}')}"


def _finish_intake(uid: int, profile: dict, stopped: bool = False) -> str:
    clear(uid)
    answered = any(topic in profile for topic in INTAKE_STEPS)
    # the interview answers are not stored chat messages, so they don't advance the "no questions back to back" count: release it
    db.set_fields(uid, intake_state="done" if answered else "skipped", question_at_count=-100)
    return _t(uid, "INTAKE_STOPPED" if stopped else "INTAKE_DONE")


def _decline(uid: int, state: dict) -> str:
    """The user said skip: stop, and don't offer this again for a while."""
    clear(uid)
    column = {"goal": "goal_asked_at", "objective": "obj_asked_at", "followup": "followup_asked_at"}[state["flow"]]
    db.set_field(uid, column, time.time())
    reply = _t(uid, {"goal": "GOAL_SKIPPED", "objective": "OBJ_SKIPPED", "followup": "FU_LATER"}[state["flow"]])
    return _with_next_setup_step(uid, reply) if state["flow"] != "followup" else reply


async def _drafting(uid: int, state: dict, text: str, said: str):
    """First answer to 'what is your goal / this week's step?': draft the wording and ask for a yes."""
    if said in SKIP or said in NO:
        return _decline(uid, state)
    kind = state["flow"]
    if kind == "goal" and state.get("options") and (pick := _picked_option(text, len(state["options"]))):
        index, rest = pick
        chosen = state["options"][index]
        if not rest:  # choosing one of the offered ideas is a yes: save it now
            return await _accept(uid, {**state, "candidate": chosen})
        revised = await llm.draft(uid, "goal", rest, previous=chosen)  # "2, but only on weekdays": their change applied to that idea
        _ask_to_confirm(uid, state, revised or chosen, revisions=1)
        return _t(uid, "GOAL_CONFIRM", goal=revised or chosen)
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
        return await _accept(uid, state)
    if said in NO and state["step"] == "confirm":
        put(uid, kind, "revise", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
        return _t(uid, "REVISE")
    revisions = state.get("revisions", 0) + 1
    if revisions > MAX_REVISIONS:  # enough rounds: take the current draft, it can be changed later
        return await _accept(uid, state)
    revised = await llm.draft(uid, kind, text, previous=candidate, goal=state.get("goal", ""))
    _ask_to_confirm(uid, state, revised or candidate, revisions)
    return _t(uid, "GOAL_CONFIRM" if kind == "goal" else "OBJ_CONFIRM", goal=revised or candidate, step=revised or candidate)


async def _accept(uid: int, state: dict) -> str:
    """The user agreed to the wording: a goal is saved now, a weekly step first asks for a reward."""
    candidate = state["candidate"]
    if state["flow"] == "goal":
        clear(uid)
        already = candidate.casefold() in {g["text"].casefold() for g in db.active_goals(uid)}
        if not already and db.add_goal(uid, candidate) is None:
            return _t(uid, "GOAL_FULL")
        return f"{_t(uid, 'GOAL_SAVED', goal=candidate)}\n\n{await start_objective(uid)}"  # the natural next question: a first small step
    put(uid, "objective", "reward", **{k: v for k, v in state.items() if k not in ("flow", "step", "started")})
    return _t(uid, "OBJ_REWARD")


async def _reward(uid: int, state: dict, text: str, said: str):
    reward = "" if said in NONE_WORDS else " ".join(text.split())[:100]
    saved = db.add_objective(uid, state.get("goal_id"), state["candidate"], reward)
    clear(uid)
    if saved is None:
        return _t(uid, "OBJ_FULL")
    saved_text = _t(uid, "OBJ_SAVED", step=state["candidate"], reward=_t(uid, "OBJ_REWARD_NOTE", reward=reward) if reward else "")
    return _with_next_setup_step(uid, saved_text)


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
