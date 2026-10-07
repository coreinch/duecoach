import time

import pytest

from coach import core, db, flows, llm, tools


@pytest.fixture
def model(monkeypatch):
    async def fake_reply(uid, text, instruction=None, coach=True):
        if text:
            db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    async def fake_draft(uid, kind, text, previous=None, goal=""):
        return f"{kind}: {text}"

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "draft", fake_draft)
    monkeypatch.setattr(llm, "cards_read", lambda uid: set())


def new_user(ext="7007", messages=0):
    """A newcomer who has agreed to the privacy notice and confirmed a timezone, with the interview still to come."""
    row = db.get_or_create_user("telegram", ext, ext, "en")
    db.set_fields(row["user_id"], consent_at=1.0, tz_set=1)
    for i in range(messages):
        db.add_message(row["user_id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
    return row["user_id"]


async def say(text, ext="7007"):
    return await core.handle_text("telegram", ext, ext, text)


ANSWERS = [
    "my mornings fall apart and I'm always late",
    "alarms and sticky notes, the notes helped a bit",
    "starting tasks and keeping track of time",
    "I'm creative and good with people",
    "I wake at 7, sleep around 1, and work 9 to 5",
    "a bit stressed but okay",
]


async def test_the_interview_starts_after_the_first_exchange_not_before(model):
    uid = new_user()
    assert await say("hi there") == "coached"  # nothing yet: the coach answers first
    reply = await say("I want to work on my mornings")
    assert reply.startswith("coached") and "a few quick questions" in reply and "what made you look for support" in reply
    assert flows.get(uid)["flow"] == "intake"


async def test_the_whole_interview_is_saved_as_a_profile_and_shown_back(model):
    uid = new_user(messages=2)
    await say("hello")  # triggers the first question
    replies = [await say(answer) for answer in ANSWERS]
    assert all(r.startswith("Thanks.") for r in replies[:-1]) and "short profile" in replies[-1]
    profile = db.get_profile(uid)
    assert [profile[t] for t in flows.INTAKE_STEPS] == ANSWERS
    user = db.get_user(uid)
    assert user["intake_state"] == "done" and flows.get(uid) is None
    shown = await say("/notes")
    assert "About you" in shown and "Why you came: my mornings fall apart" in shown and "Your strengths: I'm creative" in shown
    state = tools.coaching_state(uid)
    assert "What they told you at intake" in state and "strengths: I'm creative and good with people" in state


async def test_skip_passes_on_a_question_and_stop_ends_the_interview_keeping_what_was_said(model):
    uid = new_user(messages=2)
    await say("hello")
    await say(ANSWERS[0])
    await say("skip")  # skips 'tried'
    assert "tried" not in db.get_profile(uid)
    reply = await say("enough")
    assert "we'll stop here" in reply and flows.get(uid) is None
    assert db.get_user(uid)["intake_state"] == "done" and db.get_profile(uid)["why"] == ANSWERS[0]


async def test_stopping_before_answering_anything_counts_as_skipped(model):
    uid = new_user(messages=2)
    await say("hello")
    assert "we'll stop here" in await say("stop")
    assert db.get_user(uid)["intake_state"] == "skipped"


async def test_a_heavy_mood_gets_a_gentle_note_and_is_flagged_for_the_coach(model):
    uid = new_user(messages=2)
    await say("hello")
    for answer in ANSWERS[:-1]:
        await say(answer)
    reply = await say("honestly I've been really low and anxious for months")
    assert "doctor or therapist" in reply and db.get_profile(uid)["mood_heavy"] is True
    assert "heavy mood" in tools.coaching_state(uid)


async def test_goals_wait_for_the_interview(model):
    uid = new_user(messages=2)
    reply = await say("I always forget my keys")  # a problem is named, but the interview comes first
    assert "a few quick questions" in reply and "set a goal together" not in reply
    for answer in ANSWERS:  # the interview is answered...
        await say(answer)
    for i in range(flows.QUESTION_GAP):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"gap {i}")
    assert "set a goal together" in await say("I keep losing my wallet")  # ...and now the goal question fits


async def test_an_abandoned_interview_resumes_where_it_stopped_and_is_dropped_after_three_tries(model):
    uid = new_user(messages=2)

    def wander_off():
        """The question expires unanswered and a couple of days and exchanges go by."""
        flows.clear(uid)
        db.set_field(uid, "intake_asked_at", time.time() - flows.INTAKE_COOLDOWN - 10)
        for i in range(flows.QUESTION_GAP):
            db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"filler {i}")

    await say("hello")  # start 1
    await say(ANSWERS[0])
    wander_off()
    resumed = await say("hello again")  # start 2: picks up at the second topic
    assert "What have you already tried" in resumed and "quick questions" not in resumed
    wander_off()
    assert "What have you already tried" in await say("back again")  # start 3
    wander_off()
    assert "What have you already tried" not in await say("and again")  # a fourth time is one too many
    assert db.get_user(uid)["intake_state"] == "skipped"


async def test_intake_command_redoes_the_interview_and_forget_clears_the_profile(model):
    uid = new_user()
    db.set_profile(uid, {"why": "old answer", "tried": "old"})
    db.set_field(uid, "intake_state", "done")
    assert "a few quick questions" in await say("/intake")
    await say("a new reason")
    assert db.get_profile(uid)["why"] == "a new reason" and "tried" not in db.get_profile(uid)
    assert "Notes cleared" in await say("/forget")
    assert db.get_profile(uid) == {}


async def test_crisis_wording_in_the_middle_of_the_interview_gets_the_safety_message(model):
    uid = new_user(messages=2)
    await say("hello")
    await say(ANSWERS[0])
    reply = await say("I want to die")
    assert "112" in reply and flows.get(uid) is None
    assert "I want to die" not in db.get_profile(uid).values()


def test_people_already_using_the_bot_are_not_put_through_the_interview(tmp_path):
    path = str(tmp_path / "old.db")
    db.close()
    db.init(path)
    db._conn.execute("DELETE FROM users")
    db._conn.execute("INSERT INTO users (user_id, ext_id, channel, chat_id, tz) VALUES (42, '42', 'telegram', 42, 'Europe/Athens')")
    db._conn.execute("PRAGMA user_version=5")
    db._conn.commit()
    db.close()
    db.init(path)
    assert db.get_user(42)["intake_state"] == "skipped"
    assert db.get_or_create_user("telegram", "43", "43")["intake_state"] == ""


async def test_notes_refresh_runs_every_twenty_new_messages_even_when_the_count_is_odd(monkeypatch):
    uid = new_user()
    calls = []

    async def fake_complete(messages, max_tokens=1500):
        calls.append(1)
        return "- likes timers"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    db.add_message(uid, "assistant", "a check-in")  # one stray message makes the count odd from here on
    for i in range(18):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    await llm.refresh_notes(uid)
    assert not calls  # 19 messages: not yet
    db.add_message(uid, "user", "one more")
    db.add_message(uid, "assistant", "and another")  # 21: the old rule (== a multiple of 20) would have skipped this forever
    await llm.refresh_notes(uid)
    assert len(calls) == 1 and db.get_notes(uid) == "- likes timers" and db.get_user(uid)["notes_at_count"] == 21
    await llm.refresh_notes(uid)
    assert len(calls) == 1  # and not again until twenty more have arrived
