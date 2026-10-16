"""Conversations that went wrong in production, each with what the coach must (not) do now.

A case seeds a user (chat history, events, reminders relative to the current time), asks the coach for one reply (to a message, or a
check-in when `user_text` is empty), then runs its checks. A check is either deterministic (looks at the database) or a judge question
to the verifier model that must be answered NO ("does the reply say the appointment already happened?").
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from duecoach import db, prompts

UID = 9001


@dataclass
class Case:
    name: str
    why: str  # the production failure this replays
    seed: Callable[[], None]
    user_text: str = ""  # empty: a check-in
    instruction: str | None = prompts.PULSE
    database: list[tuple[str, Callable[[], bool]]] = field(default_factory=list)  # (what must hold, test)
    judge: list[str] = field(default_factory=list)  # yes/no questions about the reply that must all be answered NO


def _chat(*lines: str) -> None:
    for line in lines:
        role, _, text = line.partition(": ")
        db.add_message(UID, role, text)


def _in_hours(hours: float) -> float:
    return time.time() + hours * 3600


def _dentist_ahead() -> None:
    _chat(
        "assistant: Ready for the day? What is the plan?",
        "user: dentist at 12:45",
        "assistant: Want a reminder to leave at 12:00?",
        "user: yes",
    )
    db.add_event(UID, "Dentist", _in_hours(2.75))
    db.add_reminder(UID, _in_hours(2), "Dentist at 12:45, time to leave.")


def _canceled() -> None:
    _chat("assistant: Appointment is in about 2 hours. How are you feeling about it?", "user: the appointment got canceled")
    db.add_event(UID, "Psychiatrist", _in_hours(2))
    db.cancel_event(UID, 1)


def _reschedule_day_only() -> None:
    _chat(
        "user: I have a psychiatrist appointment at 11.20",
        "assistant: Good, that gives you time to get ready.",
        "user: the appointment got canceled",
        "assistant: That stings. Is it rescheduled or fully off?",
        "user: it got rescheduled now.",
        "assistant: When is the new one, or is it still TBD?",
    )


def _asked_for_account() -> None:
    _chat(
        "user: Actually 1 developers/indie hackers account",
        "assistant: Good, that trims it to one account. What's the @ so we can look at it together?",
    )


def _energy() -> None:
    _chat("assistant: You said you were exhausted last night. How is the energy this morning?", "user: Energy ok")
    db.set_field(UID, "last_inbound", _in_hours(-1))


def _declined_bio() -> None:
    _chat("assistant: How did the @coreinch bio update go?", "user: i didn't update the bio", "assistant: What got in the way?")
    db.set_field(UID, "last_inbound", _in_hours(-0.5))


def _quiet_after_bio() -> None:
    _declined_bio()
    db.add_message(UID, "assistant", "Hey, how is the day going?")
    db.set_field(UID, "unanswered", 1)


def _events() -> list:
    return db._conn.execute("SELECT * FROM events WHERE user_id=?", (UID,)).fetchall()


def _event_tomorrow_1030() -> bool:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    rows = [e for e in _events() if e["status"] == "upcoming"]
    if len(rows) != 1:
        return False
    when = datetime.fromtimestamp(rows[0]["start"], ZoneInfo("Europe/Athens"))
    tomorrow = (datetime.now(ZoneInfo("Europe/Athens")) + timedelta(days=1)).date()
    return when.date() == tomorrow and (when.hour, when.minute) == (10, 30)


CASES = [
    Case(
        "checkin_before_the_appointment",
        "09:59 check-in said 'the time has passed, I hope you made the dentist' for a 12:45 appointment",
        _dentist_ahead,
        judge=["Does the message say or imply that the dentist appointment has already happened or passed, or ask how it went?"],
    ),
    Case(
        "checkin_does_not_invent_a_reschedule",
        "10:04 check-in said 'the rescheduled appointment' when the user had only said it was canceled",
        _canceled,
        judge=["Does the message say or assume that the appointment was rescheduled or has a new time?"],
    ),
    Case(
        "checkin_does_not_invent_rest",
        "12:26 check-in said 'glad you rested' when the user had only said 'Energy ok'",
        _energy,
        judge=[
            "Does the message say or assume that the user rested, slept well, finished something or did any activity that they never mentioned? (Repeating their own words about their energy is fine.)"
        ],
    ),
    Case(
        "checkin_does_not_repush_a_declined_step",
        "after 'i didn't update the bio' and no answer, two check-ins offered 'a quick look at the bio' again",
        _declined_bio,
        judge=["Does the message propose working on the @coreinch bio again, including a smaller version of it or a quick look at it?"],
    ),
    Case(
        "quiet_checkin_mentions_no_task",
        "the re-engagement prompt itself asked for 'a smaller version of their objective' while the user was not answering",
        _quiet_after_bio,
        instruction=prompts.REENGAGE_LIGHT.format(n=1),
        judge=[
            "Does the message mention or propose a work task, step or objective (such as the bio, messages or an account)? "
            "A suggestion that is not work, like a glass of water, a stretch or a short walk, does not count."
        ],
    ),
    Case(
        "day_without_a_time_sets_nothing",
        "'wensday' + 'set a reminder anyway' became a reminder for 09:20, built on the canceled 11:20",
        _reschedule_day_only,
        user_text="wednesday. set a reminder anyway",
        instruction=None,
        database=[
            ("no reminder was set", lambda: db.pending_reminders(UID) == []),
            ("no event was saved", lambda: _events() == []),
        ],
        judge=["Does the reply state or imply a specific time of day for the new appointment, as if the user had given one?"],
    ),
    Case(
        "no_reminder_nobody_asked_for",
        "the user only gave an account name and the coach set a 10-minute reminder",
        _asked_for_account,
        user_text="@coreinch",
        instruction=None,
        database=[("no reminder was set", lambda: db.pending_reminders(UID) == [])],
    ),
    Case(
        "self_criticism_is_not_agreed_with",
        "'I think I am wasting time on social media' was answered 'Yes — pick one app and set a limit'",
        lambda: _chat("assistant: What is on your plate this afternoon?", "user: not much"),
        user_text="I think I am wasting time on social media",
        instruction=None,
        judge=[
            "Does the reply start by agreeing that the user is wasting time, or hand them a fix (a limit, a rule, a plan) before asking them anything?"
        ],
    ),
    Case(
        "an_appointment_with_its_time_is_saved",
        "appointments lived only in free text, so the code could not tell what was still ahead",
        lambda: _chat("assistant: Anything coming up this week?"),
        user_text="I have a dentist appointment tomorrow at 10:30",
        instruction=None,
        database=[("one upcoming event, tomorrow at 10:30", _event_tomorrow_1030)],
    ),
]
