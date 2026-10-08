import time

import pytest

from duecoach import core, db, llm, ratelimit


@pytest.fixture
def ai(monkeypatch):
    """Replace the model: records what it was asked and answers 'coached'."""
    calls = []

    async def fake_reply(uid, text, instruction=None):
        calls.append((uid, text, instruction))
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)
    return calls


async def say(text, ext_id="1001", lang="en"):
    return await core.handle_text("telegram", ext_id, ext_id, text, lang)


async def test_nothing_is_processed_before_the_user_agrees(ai):
    assert "AI coaching assistant" in await say("I can't focus", "5005")
    assert "AI coaching assistant" in await say("/plan", "5005")
    assert ai == [] and db.message_count(5005) == 0
    assert "Thanks" in await say("/agree", "5005")
    assert await say("I can't focus", "5005") == "coached" and len(ai) == 1


async def test_language_can_be_chosen_before_agreeing():
    assert "Ελληνικά" in await say("/language el", "5006")
    assert "βοηθός coaching" in await say("hello", "5006")  # the notice now appears in Greek


async def test_commands_run_without_the_model(ai, user):
    assert "/stuck" in await say("/help")
    assert await say("/notes") == "Nothing yet. We're just getting started."
    assert "Europe/Athens" in await say("/timezone")
    assert "I don't know that timezone" in await say("/timezone Mars/Base")
    assert "set to Asia/Tokyo" in await say("/timezone Asia/Tokyo")
    assert ai == []


async def test_coaching_commands_and_plain_text_go_to_the_model(ai, user):
    await say("/stuck my taxes")
    await say("hello there")
    await say("/progress")
    assert [c[1] for c in ai] == ["/stuck my taxes", "hello there", "/progress"]
    await say("/unknowncommand")
    assert ai[-1][1] == "/unknowncommand"


async def test_interval_needs_a_confirmed_timezone_and_sane_numbers(ai):
    await say("/agree", "6006")
    assert "timezone" in await say("/interval on", "6006")
    await say("/timezone Europe/Athens", "6006")
    assert "every 30 minutes" in await say("/interval on", "6006")
    assert "every 60 minutes" in await say("/interval 60", "6006")
    assert "Use a number" in await say("/interval 5", "6006")
    assert "stopped" in await say("/interval off", "6006")
    assert db.get_user(6006)["interval_min"] == 0


async def test_checkin_hours_must_be_ordered(ai, user):
    assert "earlier" in await say("/morning 21")
    assert "daily check-ins start at 8:00" in await say("/morning 8")
    assert "turned off" in await say("/evening off")
    assert "start at 22:00" in await say("/morning 22")  # fine once the end hour is off


async def test_deleting_your_data_needs_confirmation(ai, user):
    db.add_message(1001, "user", "private")
    db.add_goal(1001, "goal")
    assert "permanently erases" in await say("/deletedata")
    assert db.get_user(1001) is not None
    assert "erased" in await say("/deletedata confirm")
    assert db.get_user(1001) is None and db.message_count(1001) == 0
    assert "AI coaching assistant" in await say("hi again")  # starts over, including the privacy notice


async def test_flooding_is_cut_off_with_a_single_warning(ai, user, monkeypatch):
    monkeypatch.setattr(ratelimit, "RATE_LIMIT_MESSAGES", 3)
    replies = [await say("hi") for _ in range(6)]
    assert replies[:3] == ["coached"] * 3
    assert "a lot at once" in replies[3] and replies[4:] == [None, None]


async def test_messages_from_one_user_are_handled_one_at_a_time(user, monkeypatch):
    import asyncio

    active, peak = 0, 0

    async def slow_reply(uid, text, instruction=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "ok"

    monkeypatch.setattr(llm, "reply", slow_reply)
    await asyncio.gather(*(say(f"m{i}") for i in range(4)))
    assert peak == 1
    other = db.get_or_create_user("telegram", "2002", "2002")["user_id"]
    db.set_fields(other, consent_at=1.0)
    peak = 0
    await asyncio.gather(say("a"), say("b", "2002"))
    assert peak == 2  # different users do not wait for each other


async def test_unauthorised_users_are_ignored(ai, monkeypatch):
    monkeypatch.setattr("duecoach.config.ALLOWED", {("telegram", "1")})
    assert await say("hello", "999") is None and db.get_user(999) is None


async def test_non_text_messages_get_a_polite_reply(user):
    assert "only read text" in await core.handle_unsupported("telegram", "1001", "1001")
    assert "AI coaching assistant" in await core.handle_unsupported("telegram", "7070", "7070")


async def test_writing_resets_the_back_off_counter(ai, user):
    db.set_field(1001, "unanswered", 4)
    await say("I'm back")
    assert db.get_user(1001)["unanswered"] == 0 and db.get_user(1001)["last_inbound"] > time.time() - 5
