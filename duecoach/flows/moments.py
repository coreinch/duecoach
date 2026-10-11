"""When is a good moment to ask: the patterns that spot distress and crisis wording, and whether the bot may ask a question now."""

import re

from .. import db
from ..timezones import normalize

QUESTION_GAP = 4  # stored chat messages that must pass after any question of ours before the next one (two exchanges)


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


_CRISIS, _DISTRESS = ([re.compile(p) for p in patterns] for patterns in (CRISIS_PATTERNS, DISTRESS_PATTERNS))


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
