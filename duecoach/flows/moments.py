"""Crisis wording: the one thing the bot recognises itself, because the safety reply must not depend on the model."""

import re

from ..timezones import normalize

CRISIS_PATTERNS = [
    r"\b(kill myself|suicid\w*|end(ing)? (it all|my life)|take my own life|want to die|wanna die|hurt(ing)? myself|harm(ing)? myself|self harm|no reason to live)\b",
    r"\b(don t want to (live|be here)|can t go on|better off dead)\b",
    r"\babus(ed|e|ive)\b",
    r"αυτοκτον\w*|να πεθανω|δεν θελω να ζω|τελειωσω (τα παντα|τη ζωη|ολα)|κακο στον εαυτο μου|δεν αντεχω αλλο|κακοποι\w*",
]


# softer than a crisis: the person is struggling (anxiety, overwhelm, harsh self-criticism). Not the moment for a form.


_CRISIS = [re.compile(p) for p in CRISIS_PATTERNS]


def is_crisis(text: str) -> bool:
    said = normalize(text)
    return any(p.search(said) for p in _CRISIS)
