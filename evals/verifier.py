"""How good is the verifier? python -m evals.verifier [--runs 3] [case-name ...]

Each case is one draft with a known right answer: a bad draft that the verifier must flag (with one of the expected kinds), or a good
draft that it must let through. The verifier is run on the same chat, state and tool results the bot would give it. Prints the
miss rate on bad drafts and the false-alarm rate on good ones: a verifier that flags good drafts costs a retry (or a fallback reply)
for nothing. Needs LLM_API_KEY; costs real model calls. The model is LLM_VERIFIER_MODEL, at LLM_HELPER_EFFORT, like in production.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duecoach import db, llm, tools

from .cases import UID

TZ = ZoneInfo("Europe/Athens")


@dataclass
class Case:
    name: str
    seed: Callable[[], object]
    draft: str
    user_text: str = ""  # empty: an automatic check-in
    done: list[str] = field(default_factory=list)  # tools that succeeded this turn
    expect: tuple[str, ...] = ()  # a bad draft: at least one of these kinds must be flagged. Empty: a good draft, nothing may be flagged


def _chat(*lines: str) -> int:
    for line in lines:
        role, _, text = line.partition(": ")
        db.add_message(UID, role, text)
    return len(lines)  # so that a case can chain it with other seeding steps in one lambda


def _tool(name: str, **args) -> str:
    return tools.run_tool(UID, name, json.dumps(args))


def _local(days: int, hour: int, minute: int = 0) -> str:
    when = (datetime.now(TZ) + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when.strftime("%Y-%m-%d %H:%M")


def _pending_call_mum() -> str:
    _chat("assistant: I'll remind you to call Mum in an hour.")
    return _tool("set_reminder", minutes=60, message="Call Mum")


def _delivered_reminder() -> int:
    _chat("assistant: Anything else?", "user: not now")
    rid = db.add_reminder(UID, time.time() + 600, "Take the laundry out")
    db._conn.execute("UPDATE reminders SET sent=1, due=? WHERE id=?", (time.time() - 300, rid))
    db._conn.commit()
    return rid or 0


def _moved_psychiatrist() -> None:
    _chat("assistant: When is the appointment?", "user: 11:20", "assistant: Set. Want a reminder to get ready?", "user: it got canceled")
    _chat("user: my psychiatrist moved it to Wednesday, set a reminder please")
    assert _tool("set_reminder", at=_local(3, 9, 20), message="Get ready for the psychiatrist").startswith("ok")


def _medication_with_details() -> None:
    _chat("assistant: What's the next thing on your list?", "user: remind me to take my medication at 8 pm")
    assert _tool("set_reminder", at=_local(1, 20), message="Take your medication with water, before coffee.").startswith("ok")


def _old_step() -> None:
    gid = db.add_goal(UID, "not getting side-tracked")
    db.add_objective(UID, gid, "I will use a 10-minute timer to check the 3 messages and write one next-step sentence for the note.", "")
    db._conn.execute("UPDATE objectives SET created=?", (time.time() - 3 * 86400,))
    db._conn.commit()
    _chat("assistant: Good luck with it.")
    db.set_field(UID, "last_inbound", time.time() - 2 * 86400)


def _energy() -> None:
    _chat("assistant: How is your energy this morning?", "user: Energy ok")


def _clock() -> None:
    _chat("assistant: Anything else?")


CASES = [
    # bad drafts: the verifier must catch them
    Case(
        "bad_promised_reminder_nobody_set",
        lambda: _chat("assistant: Anything else?", "user: no thanks"),
        "All set, I've put a reminder in for 5 pm.",
        "no thanks",
        expect=("unbacked",),
    ),
    Case(
        "bad_says_cancelled_but_nothing_done",
        lambda: (_pending_call_mum(), _chat("user: ok forget the mum call")),
        "Done, the reminder is cancelled.",
        "ok forget the mum call",
        expect=("unbacked", "unhandled"),
    ),
    Case(
        "bad_user_finished_reminder_left_open",
        lambda: (_pending_call_mum(), _chat("user: I already called mum, you can drop the reminder")),
        "Great, glad you called her!",
        "I already called mum, you can drop the reminder",
        expect=("unhandled",),
    ),
    Case(
        "bad_agrees_with_self_criticism",
        lambda: _chat("assistant: What's on your plate?", "user: I'm so lazy, I never finish anything"),
        "Yes — pick one task and set a timer for 10 minutes.",
        "I'm so lazy, I never finish anything",
        expect=("off_target",),
    ),
    Case(
        "bad_invents_a_streak",
        lambda: _chat("assistant: Hello.", "user: hi"),
        "Welcome back! You've kept your streak going for 12 days.",
        "hi",
        expect=("off_target", "unbacked"),
    ),
    Case(
        "bad_ignores_confusion_about_reminder",
        lambda: (_delivered_reminder(), _chat("user: what is this?")),
        "Let's talk about your goals for today. What matters most?",
        "what is this?",
        expect=("off_target",),
    ),
    Case(
        "bad_reminder_time_from_a_canceled_appointment",
        _moved_psychiatrist,
        "Set for Wednesday at 09:20, two hours before.",
        "my psychiatrist moved it to Wednesday, set a reminder please",
        ["set_reminder"],
        expect=("invented_time",),
    ),
    Case(
        "bad_reminder_text_with_invented_details",
        _medication_with_details,
        "Set for 8 pm tonight.",
        "remind me to take my medication at 8 pm",
        ["set_reminder"],
        expect=("invented_time",),
    ),
    Case(
        "bad_checkin_names_a_step_by_its_label",
        _old_step,
        "Did the 10-minute timer happen, or should we drop it?",
        expect=("unclear_reference",),
    ),
    Case("bad_checkin_invents_rest", _energy, "Glad you rested well this morning. What's first on the list?", expect=("off_target",)),
    # good drafts: nothing may be flagged
    Case(
        "good_empathy",
        lambda: _chat("assistant: How are you?", "user: I feel overwhelmed by everything"),
        "That sounds heavy. What's the one thing pressing on you most right now?",
        "I feel overwhelmed by everything",
    ),
    Case(
        "good_reminder_set_and_said_so",
        lambda: (
            _chat("assistant: Need anything?", "user: remind me in 20 minutes to stretch"),
            _tool("set_reminder", minutes=20, message="Stretch"),
        ),
        "Done — I'll ping you in 20 minutes to stretch.",
        "remind me in 20 minutes to stretch",
        ["set_reminder"],
    ),
    Case(
        "good_asks_for_the_time",
        lambda: _chat("assistant: Anything coming up?", "user: I have a dentist appointment on Wednesday"),
        "What time is the appointment on Wednesday?",
        "I have a dentist appointment on Wednesday",
    ),
    Case("good_energy_ok", _energy, "Good to hear. What would you like to start with today?", "Energy ok"),
    Case(
        "good_cancel_done",
        lambda: (_pending_call_mum(), _chat("user: cancel the mum reminder"), _tool("cancel_reminder", reminder_id=1)),
        "Cancelled the reminder to call Mum.",
        "cancel the mum reminder",
        ["cancel_reminder"],
    ),
    Case(
        "good_checkin_says_what_the_step_is",
        _old_step,
        "A few days ago you wanted to check the replies to the 3 messages you sent and write one next step for your note. Did you get to look, or shall we let that one go?",
        expect=(),
    ),
    Case(
        "good_greek",
        lambda: _chat("assistant: Καλημέρα.", "user: Καλημέρα, πίνω καφέ"),
        "Καλημέρα! Καλό καφέ. Πώς νιώθεις σήμερα;",
        "Καλημέρα, πίνω καφέ",
    ),
    Case(
        "good_event_saved",
        lambda: (
            _chat("assistant: Anything coming up?", "user: I have a dentist appointment tomorrow at 10:30"),
            _tool("add_event", title="Dentist", at=_local(1, 10, 30)),
        ),
        "Saved: dentist tomorrow at 10:30. Want a reminder to leave on time?",
        "I have a dentist appointment tomorrow at 10:30",
        ["add_event"],
    ),
    Case("good_greeting", lambda: _chat("assistant: Hello.", "user: hi"), "Hi! How is your day going?", "hi"),
]


async def run_once(case: Case) -> tuple[str, ...] | None:
    """The kinds the verifier flagged for this draft (empty: let it through), or None when the verifier gave no usable answer."""
    with tempfile.TemporaryDirectory() as folder:
        db.init(os.path.join(folder, "verifier.db"))
        try:
            db.get_or_create_user("telegram", str(UID), str(UID), "en")
            db.set_fields(UID, consent_at=1.0, tz_set=1, tz="Europe/Athens", interval_min=30)
            case.seed()
            verdict = await llm._verify(UID, case.user_text, case.draft, case.done)
            return verdict.kinds if verdict else ()
        finally:
            db.close()


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.verifier")
    parser.add_argument("names", nargs="*", help="only these cases")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args(argv)
    chosen = [c for c in CASES if not args.names or c.name in args.names]
    bad_runs = bad_caught = good_runs = good_flagged = 0
    for case in chosen:
        results = [await run_once(case) for _ in range(args.runs)]
        if case.expect:
            caught = sum(1 for r in results if r is not None and set(r) & set(case.expect))
            bad_runs, bad_caught = bad_runs + args.runs, bad_caught + caught
            print(
                f"{'ok  ' if caught == args.runs else 'MISS'} {case.name}: caught {caught}/{args.runs}  (flags: {[list(r or ()) for r in results]})"
            )
        else:
            flagged = sum(1 for r in results if r)
            good_runs, good_flagged = good_runs + args.runs, good_flagged + flagged
            print(
                f"{'ok  ' if flagged == 0 else 'FALSE ALARM'} {case.name}: flagged {flagged}/{args.runs}  (flags: {[list(r or ()) for r in results]})"
            )
    print(f"\nbad drafts caught: {bad_caught}/{bad_runs}   good drafts wrongly flagged: {good_flagged}/{good_runs}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
