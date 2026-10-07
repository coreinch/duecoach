import re
from datetime import datetime
from zoneinfo import ZoneInfo

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
    """Has agreed to the privacy notice and finished the interview, hasn't given a timezone. The model is replaced by a fixed answer."""

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


def clock_in(zone: str) -> str:
    return datetime.now(ZoneInfo(zone)).strftime("%H:%M")


async def ask(uid):
    """Reach the point where the bot has asked for the city."""
    seed_history(uid)
    reply = await say("hi")
    assert "where do you live" in reply
    return reply


async def test_the_question_is_asked_once_after_a_few_messages(new_user):
    assert await say("hello") == "coached"  # too early
    seed_history(new_user)
    reply = await say("I can't focus")
    assert reply.startswith("coached") and "where do you live" in reply
    assert db.get_user(new_user)["tz_state"] == "asked"
    assert "where do you live" not in await say("and my desk is a mess here")  # not asked twice


async def test_a_city_is_taken_as_correct_and_only_the_local_time_is_shown(new_user):
    await ask(new_user)
    reply = await say("Athens")
    assert re.search(r"It's \d\d:\d\d where you are right now, right\?", reply) and "Europe/Athens" not in reply
    user = db.get_user(new_user)
    assert (user["tz"], user["tz_set"], user["tz_state"]) == ("Europe/Athens", 1, "verify")  # in use immediately, not after a yes
    assert user["interval_min"] == 30 and "30 minutes of quiet" in reply  # check-ins switch on, and the user is told how to change them
    assert "Great, thanks" in await say("yes")
    assert db.get_user(new_user)["tz_state"] == "done" and db.get_user(new_user)["tz"] == "Europe/Athens"


async def test_carrying_on_without_answering_counts_as_agreeing(new_user):
    await ask(new_user)
    await say("Athens")
    assert await say("anyway, about my keys") == "coached"
    assert db.get_user(new_user)["tz_state"] == "done" and db.get_user(new_user)["tz"] == "Europe/Athens"


async def test_a_no_leads_to_asking_the_time_and_the_right_zone_is_worked_out_from_it(new_user):
    await ask(new_user)
    await say("Athens")
    assert "What time is it where you are" in await say("no")
    reply = await say(clock_in("Asia/Tokyo"))
    assert "I've fixed it" in reply and clock_in("Asia/Tokyo") in reply
    user = db.get_user(new_user)
    assert user["tz_state"] == "done" and ZoneInfo(user["tz"]).utcoffset(datetime.now()) is not None
    assert datetime.now(ZoneInfo(user["tz"])).utcoffset() == datetime.now(ZoneInfo("Asia/Tokyo")).utcoffset()  # same clock as Tokyo


async def test_the_corrected_zone_prefers_the_same_country_as_the_first_guess(new_user):
    await ask(new_user)
    await say("New York")
    await say("no")
    await say(clock_in("America/Chicago"))
    assert db.get_user(new_user)["tz"] == "America/Chicago"  # not some other zone that happens to share the clock


async def test_a_half_hour_zone_is_found_too(new_user):
    await ask(new_user)
    await say("Athens")
    await say("no")
    await say(clock_in("Asia/Kolkata"))
    assert db.get_user(new_user)["tz"] == "Asia/Kolkata"


@pytest.mark.parametrize("answer", ["no, I'm in Dubai", "No it's Dubai", "Dubai"])
async def test_a_no_with_a_place_in_it_is_taken_as_the_correction(new_user, answer):
    await ask(new_user)
    await say("Athens")
    reply = await say(answer)
    assert "where you are right now, right?" in reply and "check in after" not in reply  # not re-announced: they were told already
    assert db.get_user(new_user)["tz"] == "Asia/Dubai"


async def test_when_the_correction_cannot_be_worked_out_the_timezone_is_withdrawn(new_user):
    await ask(new_user)
    await say("Athens")
    await say("no")
    assert "I couldn't read that" in await say("hmm not sure")
    reply = await say("dunno")
    assert "I'll leave it for now" in reply
    user = db.get_user(new_user)
    assert (user["tz_set"], user["interval_min"], user["tz_state"]) == (0, 0, "skipped")  # no wrong clock, no night-time pings


async def test_a_country_with_several_clocks_asks_for_a_city_first(new_user):
    await ask(new_user)
    assert "spans several timezones" in await say("United States")
    assert db.get_user(new_user)["tz_set"] == 0
    assert "where you are right now, right?" in await say("Chicago")
    assert db.get_user(new_user)["tz"] == "America/Chicago"


async def test_skipping_and_failing_three_times_both_stop_the_questions(new_user):
    await ask(new_user)
    assert "check-ins start once it's set" in await say("skip")
    assert db.get_user(new_user)["tz_state"] == "skipped" and "where do you live" not in await say("another message")
    other = db.get_or_create_user("telegram", "9009", "9009")["user_id"]
    db.set_fields(other, consent_at=1.0, intake_state="done")
    db.add_goal(other, "a goal")
    db.add_objective(other, None, "a step", "")
    seed_history(other)
    await say("hi", "9009")
    assert "couldn't work out" in await say("blah", "9009")
    assert "couldn't work out" in await say("zzz", "9009")
    assert "check-ins start once it's set" in await say("qqq", "9009")
    assert db.get_user(other)["tz_set"] == 0 and db.get_user(other)["interval_min"] == 0


async def test_long_messages_and_commands_are_not_mistaken_for_the_answer(new_user):
    await ask(new_user)
    assert await say("I have been thinking about my morning routine all week and it is hard") == "coached"
    reply = await say("/timezone Asia/Tokyo")
    assert "Timezone set to Asia/Tokyo" in reply and "30 minutes of quiet" in reply  # the command also switches check-ins on, first time
    assert await say("Athens") == "coached"  # the question is over
    assert db.get_user(new_user)["tz"] == "Asia/Tokyo"


async def test_check_ins_switch_on_only_the_first_time_a_timezone_is_set(new_user):
    db.set_field(new_user, "tz_state", "skipped")
    assert "30 minutes of quiet" in await say("/timezone Asia/Tokyo")
    assert db.get_user(new_user)["interval_min"] == 30
    await say("/interval off")
    assert "30 minutes of quiet" not in await say("/timezone Europe/Athens")  # moving house doesn't switch them back on
    assert db.get_user(new_user)["interval_min"] == 0


async def test_the_coachs_timezone_tool_switches_check_ins_on_too(new_user):
    import json

    from coach import tools

    result = tools.run_tool(new_user, "set_timezone", json.dumps({"name": "Europe/Athens"}))
    assert "switched on automatically" in result and db.get_user(new_user)["interval_min"] == 30
    assert "switched on" not in tools.run_tool(new_user, "set_timezone", json.dumps({"name": "Asia/Tokyo"}))


async def test_greek_conversation(new_user):
    db.set_field(new_user, "lang", "el")
    seed_history(new_user)
    assert "πού μένεις" in await say("γεια")
    reply = await say("Θεσσαλονίκη")
    assert "σωστά;" in reply and "Europe/Athens" not in reply and "30 λεπτά" in reply
    assert "Τι ώρα είναι εκεί" in await say("όχι")
    assert "το διόρθωσα" in await say(clock_in("Asia/Tokyo"))


async def test_crisis_wording_while_the_local_time_is_being_checked_keeps_the_zone_and_ends_the_question(new_user):
    await ask(new_user)
    await say("Athens")
    assert "112" in await say("I want to die")
    user = db.get_user(new_user)
    assert user["tz"] == "Europe/Athens" and user["tz_state"] == "done"


async def test_no_question_after_a_failed_model_call_or_for_confirmed_users(new_user, monkeypatch):
    async def broken(uid, text, instruction=None, coach=True):
        raise RuntimeError("down")

    monkeypatch.setattr(llm, "reply", broken)
    seed_history(new_user)
    assert "where do you live" not in await say("hello")
    assert db.get_user(new_user)["tz_state"] == ""
    db.set_field(new_user, "tz_set", 1)

    async def fine(uid, text, instruction=None, coach=True):
        return "coached"

    monkeypatch.setattr(llm, "reply", fine)
    assert "where do you live" not in await say("hello again")
