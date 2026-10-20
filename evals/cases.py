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


def _after_a_gap() -> None:
    db.add_message(UID, "user", "I messaged 3 people who followed duecoachbot on X, to invite them to my weekly note list")
    db.add_objective(
        UID, None, "I will use a 10-minute timer tomorrow to check the 3 messages and write one next-step sentence for the note.", ""
    )
    db.add_message(UID, "assistant", "Nice, that was the brave part. I'll be here.")
    db._conn.execute("UPDATE messages SET ts=?", (time.time() - 2 * 86400,))
    # as in production: the explanation is days back, behind more than the five lines the verifier sees
    _chat(
        "assistant: Hey, how is your day going?",
        "assistant: Hey, easy check-in. No pressure at all.",
        "assistant: Quick one: anything you want to park for later?",
        "user: Bio is fine",
        "assistant: Cool. Want to talk about what would help the account move, or park it for later?",
        "user: Later",
        "assistant: Later is fine. I'll be here when you're ready.",
    )
    db.set_field(UID, "last_inbound", _in_hours(-0.5))


def _medication_user() -> None:
    db.set_fields(UID, profile='{"rhythm": "i wake up at 8:00. eat breakfast. make coffee. drink coffee very slowly"}')
    _chat("assistant: What's the next thing on your list?")


def _step_reported() -> None:
    gid = db.add_goal(UID, "not getting side-tracked")
    db.add_objective(UID, gid, "I will use a 10-minute timer to check the 3 messages and write one next-step sentence for the note.", "")
    _chat(
        "user: I messaged 3 people who followed the bot on X to invite them to my weekly note list",
        "assistant: A 10-minute timer to check the 3 messages and write one next-step sentence for the note, as a first step?",
        "user: sure",
        "assistant: Saved. Get some rest.",
        "assistant: You said energy was ok this morning. Did the 10 minutes on the 3 messages happen, or want to do it now?",
    )


def _step_reported_then_quiet() -> None:
    _step_reported()
    _chat("user: no replies", "assistant: That is normal, three days is nothing. You already did the hard part by sending.")
    db.set_field(UID, "last_inbound", _in_hours(-20))


def _no_open_step() -> bool:
    return not db.open_objectives(UID)


def _stale_reminder_step() -> None:
    gid = db.add_goal(UID, "Be on time for my commitments")
    db.add_objective(UID, gid, "I will get a reminder at 12:00 to leave on time for the dentist.", "")
    _chat("assistant: Good morning. How is your day starting?")


def _no_reminders() -> bool:
    return not db.pending_reminders(UID)


def _plain(text: str) -> bool:
    return not any(w in text.lower() for w in ("coffee", "water", "breakfast", "food", "meal"))


def _events() -> list:
    return db._conn.execute("SELECT * FROM events WHERE user_id=?", (UID,)).fetchall()


def _greek() -> None:
    db.set_field(UID, "lang", "el")


def _greek_stale_step() -> None:
    _greek()
    gid = db.add_goal(UID, "Να προλαβαίνω τις υποχρεώσεις μου")
    db.add_objective(UID, gid, "Θα λάβω υπενθύμιση στις 12:00 για να φύγω εγκαίρως.", "")
    _chat("assistant: Εδώ είμαι, χωρίς πίεση.")


def _greek_small_talk() -> None:
    _greek()
    _chat("assistant: Καλημέρα. Πώς πάει η μέρα;", "user: Καλημέρα, πίνω καφέ")


def _greek_leave_reminder() -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    _greek()
    tomorrow = (datetime.now(ZoneInfo("Europe/Athens")) + timedelta(days=1)).replace(hour=12, minute=45, second=0, microsecond=0)
    db.add_event(UID, "Οδοντίατρος", tomorrow.timestamp())
    _chat("assistant: Αύριο στις 12:45 έχεις οδοντίατρο. Θες υπενθύμιση να φύγεις;", "user: ναι")


def _reminder_tomorrow_1200() -> bool:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    rows = db.pending_reminders(UID)
    tomorrow = (datetime.now(ZoneInfo("Europe/Athens")) + timedelta(days=1)).date()
    return len(rows) == 1 and (lambda w: w.date() == tomorrow and (w.hour, w.minute) == (12, 0))(
        datetime.fromtimestamp(rows[0]["due"], ZoneInfo("Europe/Athens"))
    )


def _greek_energy() -> None:
    _greek()
    _chat("assistant: Πώς είναι η ενέργειά σου σήμερα;", "user: Ενέργεια οκ")
    db.set_field(UID, "last_inbound", _in_hours(-1.5))


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
        "checkin_after_a_gap_says_what_it_refers_to",
        "'The 10-minute timer for the 3 messages — did you get to it?' was right to ask but the user could not tell what it meant",
        _after_a_gap,
        judge=[
            "The user remembers what they themselves did and said, but not the bot's saved wording. Does the message name something "
            "only by a short label or saved wording ('the 3 messages', 'the 10-minute timer', 'the note') so that the user could "
            "not tell what it is? A description of what the user did ('the 3 people you messaged on X', 'your weekly note list') is "
            "clear, so answer NO for it. A message that mentions no earlier task is fine."
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
    Case(
        "a_reminder_says_only_what_the_user_asked",
        "'take my medication at 8' became the reminder 'Take your medication with water, before coffee.' at 8 pm",
        _medication_user,
        user_text="Remind me to take my medication every day at 8 pm",
        instruction=None,
        database=[("every reminder is just the medication", lambda: all(_plain(r["text"]) for r in db.pending_reminders(UID)))],
    ),
    Case(
        "a_reported_step_is_closed",
        "'no replies' (they had checked the 3 messages) left the step open, and two days later a check-in planned it again",
        _step_reported,
        user_text="no replies",
        instruction=None,
        database=[("the step is closed", _no_open_step)],
    ),
    Case(
        "checkin_does_not_replan_a_step_already_reported",
        "the 09:01 check-in proposed 'checking the 3 messages' again although the user had answered 'no replies' days before",
        _step_reported_then_quiet,
        instruction=prompts.MORNING,
        judge=["Does the message propose checking the 3 messages (or the people messaged) again, or ask whether that happened?"],
    ),
    Case(
        "an_appointment_without_a_time_gets_a_question_not_a_reminder",
        "'I have a dentist appointment...' made the model copy 12:00 from an old step into a reminder, four times; the user got 'say that again'",
        _stale_reminder_step,
        user_text="I have a dentist appointment today...",
        instruction=None,
        database=[("no reminder was set", _no_reminders)],
        judge=[
            "Does the reply fail to ask what time the appointment is (for example it says it did not understand, or says nothing about the time)?"
        ],
    ),
    Case(
        "greek_appointment_without_a_time_gets_a_question",
        "the Greek user said 'I have a dentist appointment...': a stale '12:00' step became a reminder four times and they got 'say it again'",
        _greek_stale_step,
        user_text="Έχω ραντεβού με τον οδοντίατρο σήμερα...",
        instruction=None,
        database=[("no reminder was set", _no_reminders)],
        judge=[
            "Does the reply fail to ask what time the appointment is (for example it says it did not understand, or says nothing about the time)?"
        ],
    ),
    Case(
        "greek_small_talk_is_natural",
        "'Τι κάνεις' was answered with 'I have no personal life like you, I am here for your work and your arrows'",
        _greek_small_talk,
        user_text="Τι κάνεις",
        instruction=None,
        judge=[
            "Does the reply contain a made-up or nonexistent Greek word, a clear grammar error, text in another language, or an odd statement about the assistant's own life?"
        ],
    ),
    Case(
        "greek_leave_reminder_is_set_at_the_asked_time",
        "the leave reminder time was mixed up with the appointment time, ending in a fallback reply",
        _greek_leave_reminder,
        user_text="Θύμισέ μου αύριο στις 12:00 να φύγω",
        instruction=None,
        database=[("one reminder, tomorrow at 12:00", _reminder_tomorrow_1200)],
        judge=["Does the reply say the reminder is at any time other than 12:00, or call the 12:00 reminder the appointment time?"],
    ),
    Case(
        "greek_checkin_does_not_invent_rest",
        "Greek check-in said 'glad you rested and had your coffee' when the user had only said 'Energy ok'",
        _greek_energy,
        judge=[
            "Does the message say or assume that the user rested, had coffee, slept well or did any activity that they never mentioned?"
        ],
    ),
]
