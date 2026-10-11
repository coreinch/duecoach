import json

import pytest

from duecoach import core, db, llm, tools


@pytest.fixture
def new_user(monkeypatch):
    """Has agreed to the privacy notice, hasn't given a timezone. The model is replaced by a fixed answer."""

    async def fake_reply(uid, text, instruction=None):
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)
    row = db.get_or_create_user("telegram", "8008", "8008", "en")
    db.set_fields(row["user_id"], consent_at=1.0)
    return 8008


async def say(text, ext="8008"):
    return await core.handle_text("telegram", ext, ext, text)


def run(uid, tool, **args):
    return tools.run_tool(uid, tool, json.dumps(args))


async def test_agreeing_asks_for_the_city_and_tells_the_coach_it_was_asked(monkeypatch):
    row = db.get_or_create_user("telegram", "8009", "8009", "en")
    reply = await say("/agree", "8009")
    assert "where do you live" in reply and "what brought you here" in reply
    assert db.get_user(row["user_id"])["tz_state"] == "asked" and not db.get_user(row["user_id"])["tz_set"]
    assert "welcome message asked" in tools.coaching_state(row["user_id"])


async def test_whatever_the_user_writes_next_goes_to_the_coach_not_to_a_parser(new_user):
    db.set_field(new_user, "tz_state", "asked")
    for text in ("Athens", "I keep jumping between tasks", "skip"):
        assert await say(text) == "coached"
    assert not db.get_user(new_user)["tz_set"]  # only the coach's tool sets it


def test_the_timezone_tool_sets_the_zone_tells_the_local_time_and_switches_check_ins_on_once(new_user):
    result = run(new_user, "set_timezone", name="Europe/Athens")
    assert result.startswith("ok: timezone set to Europe/Athens; it is ") and "switched on automatically" in result
    user = db.get_user(new_user)
    assert (user["tz"], user["tz_set"], user["tz_state"], user["interval_min"]) == ("Europe/Athens", 1, "done", 30)
    assert "switched on" not in run(new_user, "set_timezone", name="Asia/Tokyo")  # a correction does not switch anything again
    assert db.get_user(new_user)["tz"] == "Asia/Tokyo"
    assert "set from what the user told us" in tools.coaching_state(new_user)


def test_an_unknown_zone_is_an_error_the_model_can_fix(new_user):
    assert run(new_user, "set_timezone", name="Europe/Athen").startswith("error: unknown timezone")
    assert run(new_user, "set_timezone", name="Nowhere/Land").startswith("error")
    assert not db.get_user(new_user)["tz_set"]


def test_declining_is_recorded_so_the_coach_does_not_ask_again(new_user):
    assert run(new_user, "skip_timezone").startswith("ok")
    user = db.get_user(new_user)
    assert user["tz_state"] == "skipped" and not user["tz_set"] and not user["interval_min"]
    assert "declined" in tools.coaching_state(new_user)


async def test_the_timezone_command_still_works_and_switches_check_ins_on_only_the_first_time(new_user):
    assert "Timezone set to Asia/Tokyo" in await say("/timezone Asia/Tokyo")
    assert db.get_user(new_user)["interval_min"] == 30 and db.get_user(new_user)["tz_state"] == "done"
    await say("/interval off")
    assert "30 minutes of quiet" not in await say("/timezone Europe/Athens")  # moving house doesn't switch them back on
    assert db.get_user(new_user)["interval_min"] == 0
    assert "Area/City" in await say("/timezone Nowhere") or "timezone" in (await say("/timezone Nowhere")).lower()


def test_checkins_need_a_timezone_the_user_told_us(new_user):
    assert "set_timezone first" in run(new_user, "set_checkins", interval_minutes=30)
    run(new_user, "set_timezone", name="America/New_York")
    assert run(new_user, "set_checkins", interval_minutes=30).startswith("ok")
