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


async def test_the_parking_case_passes_only_when_the_idea_is_parked_and_the_reply_is_clean(scripted):
    case = next(c for c in CASES if c.name == "a_tangent_during_a_sprint_is_parked_without_asking")
    park = message(calls=[("park_idea", {"text": "look up flights to Rome"})])
    scripted += [NO_AGREEMENT, park, message("Parked: flights to Rome. Back to the report."), message("NO"), message("NO"), message("NO")]
    assert await runner.run_once(case) == []
    scripted += [NO_AGREEMENT, message("Sure, what dates are you flying?"), message("YES"), message("YES"), message("NO")]
    failed = await runner.run_once(case)
    assert "database: the idea was parked" in failed and sum(line.startswith("judge said YES") for line in failed) == 2


async def test_the_parked_idea_cases_check_the_to_do_and_the_closing(scripted):
    yes = next(c for c in CASES if c.name == "saying_yes_to_a_parked_idea_puts_it_on_the_list_and_closes_it")
    scripted += [
        NO_AGREEMENT,
        message(calls=[("add_task", {"text": "Look up flights to Rome"}), ("resolve_parked", {"parked_id": 1, "outcome": "moved"})]),
        message("On your list."),
    ]
    assert await runner.run_once(yes) == []
    scripted += [NO_AGREEMENT, message("Great, I'll remember that.")]
    assert await runner.run_once(yes) == ["database: a to-do about flights was saved", "database: the parked idea was closed"]
    drop = next(c for c in CASES if c.name == "letting_a_parked_idea_go_closes_it_without_a_to_do")
    scripted += [NO_AGREEMENT, message(calls=[("resolve_parked", {"parked_id": 1, "outcome": "dropped"})]), message("Gone."), message("NO")]
    assert await runner.run_once(drop) == []
