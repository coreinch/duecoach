import time

import pytest

from duecoach import chat, core, db, llm
from duecoach.crisis import is_crisis

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
    assert is_crisis(text)


async def test_never_during_a_crisis(model):
    make_user(8)
    reply = await say("I always feel like this and I want to die")
    assert "112" in reply  # the safety message, not a coaching reply with a question attached


async def test_crisis_wording_is_answered_without_the_model_and_stored_for_the_coach(model, monkeypatch):
    uid = make_user(6)

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("the safety reply must not depend on the model")

    monkeypatch.setattr(llm, "reply", must_not_be_called)
    reply = await say("I want to die")
    assert "112" in reply and "Are you safe right now" in reply
    user = db.get_user(uid)
    assert user["snooze_until"] > time.time() + 20 * 3600  # proactive check-ins pause for a day
    assert db.recent_messages(uid, 2)[-1]["content"] == reply  # the coach will see it when the conversation continues


async def test_crisis_wording_works_in_greek(model):
    uid = make_user(8)
    db.set_fields(uid, tz_set=0, lang="el")
    reply = await say("δεν αντέχω άλλο")
    assert "112" in reply and "ασφαλής" in reply


async def test_a_configured_local_helpline_is_added(model, monkeypatch):
    make_user(8)
    monkeypatch.setattr(chat, "CRISIS_HELP", "In Greece you can also call 1018.")
    assert "In Greece you can also call 1018." in await say("I want to kill myself")


async def test_someone_in_crisis_gets_the_safety_message_even_before_agreeing_and_nothing_is_stored(model):
    row = db.get_or_create_user("telegram", "6006", "6006", "en")  # new: has not agreed to the privacy notice yet
    reply = await say("I want to end my life", "6006")
    assert "112" in reply and "AI coaching assistant" not in reply
    assert db.message_count(row["user_id"]) == 0


async def test_wording_the_list_does_not_know_is_judged_by_the_model(model, monkeypatch):
    uid = make_user(8)
    asked = []

    async def sees_a_crisis(user_id, text):
        asked.append(text)
        return True

    monkeypatch.setattr(llm, "signals_crisis", sees_a_crisis)
    reply = await say("ya no le veo sentido a seguir")
    assert asked == ["ya no le veo sentido a seguir"] and "112" in reply
    assert db.get_user(uid)["snooze_until"] > time.time() + 20 * 3600


async def test_the_model_is_not_asked_when_the_list_already_matched_or_for_commands_or_before_consent(model, monkeypatch):
    asked = []

    async def records(user_id, text):
        asked.append(text)
        return False

    monkeypatch.setattr(llm, "signals_crisis", records)
    make_user(8)
    await say("I want to die")  # the list matched
    await say("/help")  # a command
    db.get_or_create_user("telegram", "6006", "6006", "en")
    await say("hello there", "6006")  # no consent yet: nothing may go to the provider
    assert asked == []
    await say("a normal message")
    assert asked == ["a normal message"]


async def test_a_failing_safety_check_never_blocks_a_normal_reply(model, monkeypatch):
    from tests.conftest import REAL_SIGNALS_CRISIS

    async def broken(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "signals_crisis", REAL_SIGNALS_CRISIS)
    monkeypatch.setattr(llm, "_complete", broken)
    make_user(8)
    assert await say("a normal message") == "coached"
