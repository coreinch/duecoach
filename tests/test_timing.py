import time

import pytest

from duecoach import chat, core, db, flows, llm

ASKS_FOR_TIMEZONE = "where do you live"


@pytest.fixture
def model(monkeypatch):
    """Coaching replies are fixed."""

    async def fake_reply(uid, text, instruction=None):
        if text:  # like the real model call: both sides of the exchange are stored
            db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)


def make_user(messages, ext="5005"):
    row = db.get_or_create_user("telegram", ext, ext, "en")
    db.set_fields(row["user_id"], consent_at=1.0)
    for i in range(messages):
        db.add_message(row["user_id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
    return row["user_id"]


async def say(text, ext="5005"):
    return await core.handle_text("telegram", ext, ext, text)


@pytest.mark.parametrize(
    "text",
    [
        "I want to die",
        "I'm thinking of ending my life",
        "i want to kill myself",
        "δεν αντέχω άλλο",
        "θέλω να πεθάνω",
        "I was abused as a kid",
    ],
)
def test_distress_is_recognised_in_both_languages(text):
    assert flows.is_crisis(text)


async def test_the_timezone_is_not_asked_in_the_first_exchanges(model):
    make_user(0)
    assert await say("hello") == "coached"
    assert await say("I keep losing my keys") == "coached"


async def test_the_timezone_is_asked_after_about_three_exchanges(model):
    make_user(6)
    assert ASKS_FOR_TIMEZONE in await say("let me think about it")


async def test_never_during_a_crisis(model):
    uid = make_user(8)
    reply = await say("I always feel like this and I want to die")
    assert "112" in reply and flows.get(uid) is None  # the safety message, not a coaching reply with a question attached


async def test_never_when_the_person_is_struggling(model):
    uid = make_user(8)
    assert await say("I always get so anxious before work") == "coached"
    assert flows.get(uid) is None and db.get_user(uid)["question_at_count"] == -100


@pytest.mark.parametrize("question_mark", ["?", ";", "\u037e"])
async def test_not_when_the_user_asked_a_question_themselves(model, question_mark):
    make_user(8)
    assert await say(f"I forget things, what can I do{question_mark}") == "coached"


async def test_questions_are_never_back_to_back(model):
    uid = make_user(8)
    assert ASKS_FOR_TIMEZONE in await say("let me think about it")
    flows.clear(uid)  # (they ignored it, but it is still our last question)
    assert await say("I keep losing my wallet too") == "coached"
    for i in range(flows.QUESTION_GAP):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"later {i}")
    assert ASKS_FOR_TIMEZONE in await say("and my phone")


async def test_the_coach_is_told_not_to_ask_its_own_question_when_ours_follows(model, monkeypatch):
    seen = []

    async def recording_reply(uid, text, instruction=None):
        seen.append(instruction)
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "reply", recording_reply)
    make_user(0)
    await say("hello there")  # nothing due: no instruction
    assert seen[-1] is None
    make_user(6, ext="5006")
    reply = await say("hello again", "5006")  # the timezone question will follow
    assert ASKS_FOR_TIMEZONE in reply and "Do NOT end your reply with a question" in seen[-1]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Put keys by the door. Do you have a bowl?", "Put keys by the door."),
        ("Good start. Try a bowl. What would work for you? And when?", "Good start. Try a bowl."),
        ("Βάλε τα κλειδιά στην πόρτα. Έχεις μπολ;", "Βάλε τα κλειδιά στην πόρτα."),
        ("A tip.\n\nWhat is your setup?", "A tip."),
        ("Do you have a bowl?", "Do you have a bowl?"),  # a lone question is kept: never send nothing
        ("Put keys by the door.", "Put keys by the door."),
    ],
)
def test_the_coachs_closing_question_is_dropped_when_ours_follows(reply, expected):
    assert chat.without_trailing_question(reply) == expected


async def test_the_reply_and_our_question_do_not_both_end_with_a_question(model, monkeypatch):
    async def asks_anyway(uid, text, instruction=None):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "x")
        return "Keep them in a bowl by the door. Do you have one?"

    monkeypatch.setattr(llm, "reply", asks_anyway)
    make_user(6)
    reply = await say("I always forget my keys")
    assert reply.startswith("Keep them in a bowl by the door.\n\n") and "Do you have one" not in reply and ASKS_FOR_TIMEZONE in reply


async def test_crisis_wording_never_becomes_the_answer_to_a_pending_question(model, monkeypatch):
    uid = make_user(6)
    assert ASKS_FOR_TIMEZONE in await say("let me think about it")
    assert flows.get(uid) is not None

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("the safety reply must not depend on the model")

    monkeypatch.setattr(llm, "reply", must_not_be_called)
    reply = await say("I want to die")
    assert "112" in reply and "Are you safe right now" in reply
    assert flows.get(uid) is None and db.get_user(uid)["tz_state"] == ""
    user = db.get_user(uid)
    assert user["snooze_until"] > time.time() + 20 * 3600  # proactive check-ins pause for a day
    assert db.recent_messages(uid, 2)[-1]["content"] == reply  # the coach will see it when the conversation continues


async def test_crisis_wording_also_cancels_the_timezone_question_and_works_in_greek(model):
    uid = make_user(8)
    db.set_fields(uid, tz_set=0, lang="el")
    flows.put(uid, "timezone", "asked", attempts=0)
    reply = await say("δεν αντέχω άλλο")
    assert "112" in reply and "ασφαλής" in reply
    assert db.get_user(uid)["tz_state"] == "" and flows.get(uid) is None


async def test_a_configured_local_helpline_is_added(model, monkeypatch):
    make_user(8)
    monkeypatch.setattr(chat, "CRISIS_HELP", "In Greece you can also call 1018.")
    assert "In Greece you can also call 1018." in await say("I want to kill myself")


async def test_someone_in_crisis_gets_the_safety_message_even_before_agreeing_and_nothing_is_stored(model):
    row = db.get_or_create_user("telegram", "6006", "6006", "en")  # new: has not agreed to the privacy notice yet
    reply = await say("I want to end my life", "6006")
    assert "112" in reply and "AI coaching assistant" not in reply
    assert db.message_count(row["user_id"]) == 0
