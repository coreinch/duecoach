import pytest

from coach import core, db, llm, timezones


@pytest.mark.parametrize(
    ("text", "zone"),
    [
        ("Athens", "Europe/Athens"),
        ("athens, greece", "Europe/Athens"),
        ("I live in New York", "America/New_York"),
        ("Αθήνα", "Europe/Athens"),
        ("Θεσσαλονίκη", "Europe/Athens"),
        ("Ελλάδα", "Europe/Athens"),
        ("Greece", "Europe/Athens"),
        ("Thessaloniki", "Europe/Athens"),
        ("São Paulo", "America/Sao_Paulo"),
        ("Manchester", "Europe/London"),
        ("Germany", "Europe/Berlin"),  # Berlin and Busingen share a clock: no follow-up question needed
        ("Cyprus", "Asia/Nicosia"),
        ("Japan", "Asia/Tokyo"),  # not the legacy zone name "Japan"
        ("europe/athens", "Europe/Athens"),
        ("I'm in Cyprus", "Asia/Nicosia"),
    ],
)
def test_places_resolve_to_a_timezone(text, zone):
    assert timezones.resolve(text).zone == zone


@pytest.mark.parametrize("text", ["United States", "usa", "Brazil", "Russia", "Australia"])
def test_countries_with_several_clocks_ask_for_a_city(text):
    result = timezones.resolve(text)
    assert result.zone is None and result.country


@pytest.mark.parametrize("text", ["bananas", "lol", "ok", "no", "Springfield", ""])
def test_things_that_are_not_places_resolve_to_nothing(text):
    result = timezones.resolve(text)
    assert result.zone is None and result.country is None


def seed_history(uid, n=6):
    for i in range(n):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"m{i}")


@pytest.fixture
def new_user(monkeypatch):
    """Has agreed to the privacy notice, hasn't confirmed a timezone. The model is replaced by a fixed answer."""

    async def fake_reply(uid, text, instruction=None, coach=True):
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)
    row = db.get_or_create_user("telegram", "8008", "8008", "en")
    db.set_fields(row["user_id"], consent_at=1.0, intake_state="done")
    db.add_goal(row["user_id"], "a goal")  # so the goal and step questions don't come before the timezone one
    db.add_objective(row["user_id"], None, "a step", "")
    return 8008


async def say(text, ext="8008"):
    return await core.handle_text("telegram", ext, ext, text)


async def test_the_question_is_asked_once_after_a_few_messages(new_user):
    assert await say("hello") == "coached"  # too early
    seed_history(new_user)
    reply = await say("I can't focus")
    assert reply.startswith("coached") and "where do you live" in reply
    assert db.get_user(new_user)["tz_state"] == "asked"
    assert "where do you live" not in await say("and my desk is a mess here")  # not asked twice


async def test_confirming_a_city_saves_the_timezone(new_user):
    seed_history(new_user)
    await say("hi")  # triggers the question
    confirm = await say("Athens")
    assert "Europe/Athens" in confirm and "right now" in confirm and db.get_user(new_user)["tz_set"] == 0
    done = await say("yes")
    user = db.get_user(new_user)
    assert "Timezone set to Europe/Athens" in done and "/interval on" in done
    assert user["tz"] == "Europe/Athens" and user["tz_set"] == 1 and user["tz_state"] == "done"


async def test_saying_no_asks_again_and_a_country_with_many_zones_asks_for_a_city(new_user):
    seed_history(new_user)
    await say("hi")
    assert "spans several timezones" in await say("United States")
    assert "America/New_York" in await say("New York")
    assert "which city or country" in await say("no")
    assert "Asia/Tokyo" in await say("Tokyo")
    await say("yes")
    assert db.get_user(new_user)["tz"] == "Asia/Tokyo"


async def test_skipping_and_failing_three_times_both_stop_the_questions(new_user):
    seed_history(new_user)
    await say("hi")
    assert "Check-ins stay off" in await say("skip")
    assert db.get_user(new_user)["tz_state"] == "skipped" and "where do you live" not in await say("another message")
    other = db.get_or_create_user("telegram", "9009", "9009")["user_id"]
    db.set_fields(other, consent_at=1.0, intake_state="done")
    db.add_goal(other, "a goal")
    db.add_objective(other, None, "a step", "")
    seed_history(other)
    await say("hi", "9009")
    assert "couldn't work out" in await say("blah", "9009")
    assert "couldn't work out" in await say("zzz", "9009")
    assert "Check-ins stay off" in await say("qqq", "9009")
    assert db.get_user(other)["tz_set"] == 0


async def test_long_messages_and_commands_are_not_mistaken_for_the_answer(new_user):
    seed_history(new_user)
    await say("hi")
    assert await say("I have been thinking about my morning routine all week and it is hard") == "coached"
    assert "Timezone set to Asia/Tokyo" in await say("/timezone Asia/Tokyo")
    assert await say("Athens") == "coached"  # confirmed through the command: the question is over
    assert db.get_user(new_user)["tz"] == "Asia/Tokyo"


async def test_greek_conversation(new_user):
    db.set_field(new_user, "lang", "el")
    seed_history(new_user)
    assert "πού μένεις" in await say("γεια")
    assert "Europe/Athens" in await say("Θεσσαλονίκη")
    assert "ορίστηκε σε Europe/Athens" in await say("ναι")


async def test_no_question_after_a_failed_model_call_or_for_confirmed_users(new_user, monkeypatch):
    async def broken(uid, text, instruction=None, coach=True):
        raise RuntimeError("down")

    monkeypatch.setattr(llm, "reply", broken)
    seed_history(new_user)
    assert "where do you live" not in await say("hello")
    assert db.get_user(new_user)["tz_state"] == ""
    db.set_field(new_user, "tz_set", 1)
    monkeypatch.setattr(llm, "reply", lambda *a, **k: _ok())
    assert "where do you live" not in await say("hello again")


async def _ok():
    return "coached"
