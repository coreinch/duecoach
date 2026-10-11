import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

import pytest

from duecoach import bot, channels, core, db, flows, llm


@pytest.fixture
def model(monkeypatch):
    """The model is replaced: coaching replies record what they were asked."""
    log = []

    async def fake_reply(uid, text, instruction=None):
        log.append((text, instruction))
        return "coached"

    monkeypatch.setattr(llm, "reply", fake_reply)
    return log


@pytest.fixture
def newcomer(model):
    """Someone who has not agreed to the privacy notice yet."""
    return db.get_or_create_user("telegram", "4004", "4004", "en")["user_id"]


@pytest.fixture
def person(newcomer):
    """Agreed, timezone not set, three exchanges of chat so far."""
    db.set_fields(newcomer, consent_at=1.0)
    for i in range(6):
        db.add_message(newcomer, "user" if i % 2 == 0 else "assistant", f"m{i}")
    return newcomer


async def say(text, ext="4004"):
    return await core.handle_text("telegram", ext, ext, text)


async def test_agreeing_asks_for_the_timezone_first(newcomer):
    reply = await say("/agree")
    assert "where do you live" in reply and "what brought you here" in reply
    assert flows.get(newcomer)["flow"] == "timezone"


async def test_a_city_answer_sets_the_timezone_and_switches_checkins_on(newcomer):
    await say("/agree")
    reply = await say("Athens")
    user = db.get_user(newcomer)
    assert user["tz"] == "Europe/Athens" and user["tz_set"] == 1 and user["interval_min"] > 0
    assert "Europe/Athens" in reply and flows.get(newcomer) is None and user["tz_state"] == "done"  # nothing waits for a "yes"


async def test_skipping_the_timezone_leaves_checkins_off(newcomer):
    await say("/agree")
    await say("skip")
    user = db.get_user(newcomer)
    assert user["tz_state"] == "skipped" and not user["tz_set"] and not user["interval_min"]
    assert flows.get(newcomer) is None


async def test_telling_the_story_instead_of_answering_gets_a_coach_reply_and_keeps_the_question_open(newcomer, model):
    await say("/agree")
    reply = await say("I keep jumping between tasks and never finish what I intend to")
    assert reply == "coached"  # no questionnaire, no goal form: just the coach
    assert flows.get(newcomer)["flow"] == "timezone"


async def test_the_bot_asks_no_goal_or_step_question_of_its_own(person, model):
    db.set_fields(person, tz_set=1, tz_state="done")
    reply = await say("I always forget to do the things I want to do")
    assert reply == "coached" and flows.get(person) is None
    assert await say("ok, I'll try that tomorrow") == "coached" and flows.get(person) is None


async def test_the_timezone_is_asked_again_after_a_few_messages_if_it_was_never_settled(person, model):
    assert "where do you live" in await say("hello")
    assert flows.get(person)["step"] == "asked"


async def test_an_unanswered_question_expires_after_a_day(newcomer):
    await say("/agree")
    state = json.loads(db.get_user(newcomer)["flow_state"])
    state["started"] = time.time() - flows.FLOW_TTL - 10
    db.set_field(newcomer, "flow_state", json.dumps(state))
    assert flows.get(newcomer) is None and db.get_user(newcomer)["flow_state"] == ""


async def test_commands_do_not_answer_a_pending_question(newcomer):
    await say("/agree")
    assert "/stuck" in await say("/help")
    assert flows.get(newcomer)["step"] == "asked"


async def test_checkins_wait_while_the_timezone_question_is_pending(person, monkeypatch):
    sent = []

    class Fake:
        name = "telegram"

        def can_send(self, u, template_ok=True):
            return True

        async def send(self, u, text):
            sent.append(text)

    monkeypatch.setitem(channels.REGISTRY, "telegram", Fake())
    db.set_fields(person, tz_set=1, interval_min=30, last_inbound=0, last_proactive=0)
    flows.put(person, "timezone", "asked", attempts=0)
    assert bot.plan_checkin(db.get_user(person), time.time(), _midday()) is None  # a question is already waiting
    flows.clear(person)
    await bot.checkin(_make_due(person))
    assert sent == ["coached"]


def _midday():
    return dt.datetime.now(ZoneInfo("Europe/Athens")).replace(hour=12, minute=0)


def _make_due(uid):
    """Open the daily window and the interval gate no matter what time the test runs."""
    db.set_fields(uid, morning_hour=0, evening_hour=23, last_morning=_midday().date().isoformat(), tz="Europe/Athens")
    return db.get_user(uid)
