import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from duecoach import core, db, flows, llm, timezones


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

    async def fake_reply(uid, text, instruction=None):
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)
    row = db.get_or_create_user("telegram", "8008", "8008", "en")
    db.set_fields(row["user_id"], consent_at=1.0)
    db.add_goal(row["user_id"], "a goal")  # so the goal and step questions don't come before the timezone one
    db.add_objective(row["user_id"], None, "a step", "")
    return 8008


async def say(text, ext="8008"):
    return await core.handle_text("telegram", ext, ext, text)


def clock_in(zone: str) -> str:
    return datetime.now(ZoneInfo(zone)).strftime("%H:%M")


async def ask(uid):
    """Reach the point where the bot has asked for the city (it does so right after /agree)."""
    reply = flows.ask_timezone(uid, "AGREED")
    assert flows.get(uid)["step"] == "asked"
    return reply


async def test_a_city_is_taken_as_correct_and_the_zone_and_local_time_are_shown(new_user):
    await ask(new_user)
    reply = await say("Athens")
    assert re.search(r"set your timezone to Europe/Athens: it's \d\d:\d\d there now", reply)
    user = db.get_user(new_user)
    assert (user["tz"], user["tz_set"], user["tz_state"]) == ("Europe/Athens", 1, "done")
    assert flows.get(new_user) is None  # nothing is waiting for a "yes"
    assert user["interval_min"] == 30 and "30 minutes of quiet" in reply  # check-ins switch on, and the user is told how to change them


async def test_a_wrong_zone_is_fixed_by_the_coach_with_the_tool_from_a_place_or_the_time(new_user):
    import json

    from duecoach import tools

    await ask(new_user)
    await say("Athens")
    assert await say("no, I'm in Dubai") == "coached"  # ordinary chat now: the model fixes it
    assert tools.run_tool(new_user, "set_timezone", json.dumps({"name": "Asia/Dubai"})).startswith("ok")
    assert db.get_user(new_user)["tz"] == "Asia/Dubai"
    assert tools.run_tool(new_user, "set_timezone", json.dumps({"local_time": clock_in("Asia/Tokyo")})).startswith("ok")
    now = datetime.now()
    assert ZoneInfo(db.get_user(new_user)["tz"]).utcoffset(now) == ZoneInfo("Asia/Tokyo").utcoffset(now)  # same clock as Tokyo


async def test_the_time_fix_prefers_the_same_country_as_the_current_zone(new_user):
    import json

    from duecoach import tools

    await ask(new_user)
    await say("New York")
    tools.run_tool(new_user, "set_timezone", json.dumps({"local_time": clock_in("America/Chicago")}))
    assert db.get_user(new_user)["tz"] == "America/Chicago"


async def test_a_time_that_gives_no_zone_or_no_arguments_is_an_error(new_user):
    import json

    from duecoach import tools

    assert tools.run_tool(new_user, "set_timezone", json.dumps({"local_time": "banana"})).startswith("error")
    assert tools.run_tool(new_user, "set_timezone", json.dumps({})).startswith("error")


async def test_a_country_with_several_clocks_asks_for_a_city_first(new_user):
    await ask(new_user)
    assert "spans several timezones" in await say("United States")
    assert db.get_user(new_user)["tz_set"] == 0
    assert "timezone to America/Chicago" in await say("Chicago")
    assert db.get_user(new_user)["tz"] == "America/Chicago"


async def test_skipping_and_failing_three_times_both_stop_the_questions(new_user):
    await ask(new_user)
    assert "check-ins start once it's set" in await say("skip")
    assert db.get_user(new_user)["tz_state"] == "skipped" and "where do you live" not in await say("another message")
    other = db.get_or_create_user("telegram", "9009", "9009")["user_id"]
    db.set_fields(other, consent_at=1.0)
    flows.ask_timezone(other)
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

    from duecoach import tools

    result = tools.run_tool(new_user, "set_timezone", json.dumps({"name": "Europe/Athens"}))
    assert "switched on automatically" in result and db.get_user(new_user)["interval_min"] == 30
    assert "switched on" not in tools.run_tool(new_user, "set_timezone", json.dumps({"name": "Asia/Tokyo"}))


async def test_greek_conversation(new_user):
    db.set_field(new_user, "lang", "el")
    assert "πού μένεις" in await ask(new_user)
    reply = await say("Θεσσαλονίκη")
    assert "Europe/Athens" in reply and "όρισα τη ζώνη ώρας" in reply and "30 λεπτά" in reply


async def test_crisis_wording_after_the_zone_was_set_keeps_the_zone(new_user):
    await ask(new_user)
    await say("Athens")
    assert "112" in await say("I want to die")
    user = db.get_user(new_user)
    assert user["tz"] == "Europe/Athens" and user["tz_state"] == "done"


async def test_the_coach_is_told_whether_the_timezone_is_set_and_whether_to_ask(new_user):
    from duecoach import tools

    assert "ask for their city once" in tools.coaching_state(new_user)
    flows.ask_timezone(new_user)
    assert "has just asked" in tools.coaching_state(new_user)
    await say("skip")
    assert "declined" in tools.coaching_state(new_user)
    other = db.get_or_create_user("telegram", "9010", "9010")["user_id"]
    db.confirm_timezone(other, "Asia/Tokyo")
    assert "set from what the user told us" in tools.coaching_state(other)
