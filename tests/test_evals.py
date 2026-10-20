"""The replay harness itself, with a scripted model: that every case seeds cleanly and the checks fire. (The real run is `python -m evals`.)"""

import json
from types import SimpleNamespace as NS

import pytest

from duecoach import db, llm
from evals import __main__ as runner
from evals.cases import CASES, UID


def message(content=None, calls=()):
    tool_calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)] or None
    return NS(content=content, tool_calls=tool_calls)


NO_AGREEMENT = message('{"goal": null, "step": null}')  # the first call of every reply to a user message: nothing to record


@pytest.fixture
def scripted(monkeypatch):
    queue = []

    async def fake_chat(messages, tool_defs=None, max_tokens=1500, model=None, effort=None):
        return queue.pop(0)

    async def fake_complete(messages, max_tokens=1500, model=None):
        return queue.pop(0).content

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.setattr(llm, "_complete", fake_complete)
    return queue


def test_the_case_names_are_unique_and_each_says_which_failure_it_replays():
    assert len({c.name for c in CASES}) == len(CASES)
    assert all(c.why and (c.database or c.judge) for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_case_seeds_without_error(case):
    db.get_or_create_user("telegram", str(UID), str(UID), "en")
    db.set_fields(UID, consent_at=1.0, tz_set=1, tz="Europe/Athens")
    case.seed()


async def test_a_case_fails_when_the_reply_trips_the_judge_and_passes_when_it_does_not(scripted):
    case = next(c for c in CASES if c.name == "checkin_before_the_appointment")
    scripted += [message("How did the dentist go?"), message("YES")]
    assert any("judge said YES" in line for line in await runner.run_once(case))
    scripted += [message("Ready to leave on time for the dentist?"), message("NO")]
    assert await runner.run_once(case) == []


async def test_a_database_check_fails_when_the_coach_set_a_reminder_nobody_asked_for(scripted):
    case = next(c for c in CASES if c.name == "no_reminder_nobody_asked_for")
    scripted += [NO_AGREEMENT, message(calls=[("set_reminder", {"minutes": 10, "message": "Open the account."})]), message("Reminder set.")]
    assert await runner.run_once(case) == ["database: no reminder was set"]
    scripted += [NO_AGREEMENT, message("Which post shows your angle best?")]
    assert await runner.run_once(case) == []


async def test_the_event_case_passes_only_for_the_right_day_and_time(scripted):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    case = next(c for c in CASES if c.name == "an_appointment_with_its_time_is_saved")
    tomorrow = (datetime.now(ZoneInfo("Europe/Athens")) + timedelta(days=1)).strftime("%Y-%m-%d")
    scripted += [
        NO_AGREEMENT,
        message(calls=[("add_event", {"title": "Dentist", "at": f"{tomorrow} 10:30"})]),
        message("Saved for tomorrow at 10:30."),
    ]
    assert await runner.run_once(case) == []
    scripted += [NO_AGREEMENT, message(calls=[("add_event", {"title": "Dentist", "at": f"{tomorrow} 09:00"})]), message("Saved.")]
    assert await runner.run_once(case) == ["database: one upcoming event, tomorrow at 10:30"]


def test_the_runner_lists_unknown_cases(capsys):
    import asyncio

    assert asyncio.run(runner.main(["nope"])) == 2
    assert "no such case" in capsys.readouterr().out
    json.dumps([c.name for c in CASES])


async def test_an_outage_during_a_check_in_case_is_a_failure_not_a_pass(monkeypatch):
    case = next(c for c in CASES if c.name == "checkin_before_the_appointment")

    async def outage(uid, text, instruction=None):
        raise llm.ModelError("MiniMax-M3: APITimeoutError")

    async def withheld(uid, text, instruction=None):
        raise llm.CheckinWithheld("still flagged")

    monkeypatch.setattr(llm, "reply", outage)
    assert (await runner.run_once(case))[0].startswith("the model failed")
    monkeypatch.setattr(llm, "reply", withheld)
    assert await runner.run_once(case) == []
