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


async def test_never_during_a_crisis(model):
    uid = make_user(8)
    reply = await say("I always feel like this and I want to die")
    assert "112" in reply and flows.get(uid) is None  # the safety message, not a coaching reply with a question attached


async def test_crisis_wording_never_becomes_the_answer_to_a_pending_question(model, monkeypatch):
    uid = make_user(6)
    flows.ask_timezone(uid)  # the timezone question is waiting for an answer
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
