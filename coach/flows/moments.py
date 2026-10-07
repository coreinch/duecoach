"""When is a good moment to ask: the patterns that spot problems, steps, distress and crisis wording, and which question suits now."""

import re
import time

from .. import db
from ..timezones import normalize
from .state import get

GOAL_MIN_MESSAGES = 2  # stored chat messages (both sides) before this exchange: the coach has answered once already...


GOAL_FALLBACK_MESSAGES = 6  # ...and if the user never states a problem or wish, ask anyway after about three exchanges


QUESTION_GAP = 4  # stored chat messages that must pass after any question of ours before the next one (two exchanges)


STEP_OFFER_COOLDOWN = 6 * 3600  # after "skip" on a step, don't offer to save one they agreed to for a few hours


GOAL_COOLDOWN = 7 * 86400  # after "skip", wait this long before offering again


OBJECTIVE_COOLDOWN = 2 * 86400


FOLLOWUP_AFTER = 2 * 86400  # a weekly step is asked about once it is this old...


FOLLOWUP_COOLDOWN = 20 * 3600  # ...and then at most once per ~day


INTAKE_MIN_MESSAGES = 2  # stored chat messages before this exchange: the coach has answered once before the interview starts


INTAKE_COOLDOWN = 2 * 86400  # an interview that was left unfinished is offered again after this long...


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


# softer than a crisis: the person is struggling (anxiety, overwhelm, harsh self-criticism). Not the moment for a form.
DISTRESS_PATTERNS = [
    r"anxi\w*|panic\w*|overwhelm\w*|too much|can t cope|drowning|hopeless|give up|giving up|worthless|useless|stupid|failure|hate myself",
    r"what s wrong with me|always (mess|screw)\w* (it |everything )?up|can t do anything right|burn(ed|t)? out|breaking down|falling apart",
    r"αγχ\w*|πανικ\w*|με πνιγ\w*|δεν τα βγαζω|τα παρατω|αχρηστ\w*|χαζ\w*|αποτυχ\w*|απελπισ\w*|τι φταιει σε μενα|τα χαλαω παντα",
]


_PROBLEM, _STEP, _CRISIS, _DISTRESS = (
    [re.compile(p) for p in patterns] for patterns in (PROBLEM_PATTERNS, STEP_PATTERNS, CRISIS_PATTERNS, DISTRESS_PATTERNS)
)


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


def is_distress(text: str) -> bool:
    said = normalize(text)
    return any(p.search(said) for p in _DISTRESS)


def question_blocked(uid: int, text: str) -> bool:
    """True when this is not the moment for a question of the bot's own."""
    if is_crisis(text) or is_distress(text):
        return True  # someone struggling needs the coach, not a form
    if text.strip().endswith(("?", ";", "\u037e")):
        return True  # they asked something themselves: answer it and stop
    user = db.get_user(uid)
    return db.message_count(uid) - (user["question_at_count"] if user["question_at_count"] is not None else -100) < QUESTION_GAP


def note_question_asked(uid: int) -> None:
    db.set_field(uid, "question_at_count", db.message_count(uid))


def due_followup(uid: int):
    """The oldest open weekly step that is old enough to ask about, unless we asked recently."""
    user = db.get_user(uid)
    now = time.time()
    if now - (user["followup_asked_at"] or 0) < FOLLOWUP_COOLDOWN:
        return None
    return next((o for o in db.open_objectives(uid) if now - o["created"] >= FOLLOWUP_AFTER), None)


def question_due(uid: int, text: str) -> str | None:
    """Which question would suit this moment: 'followup', 'goal', 'step_offer', 'step', or None. Changes nothing."""
    if get(uid):
        return None
    if due_followup(uid) is not None:
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
