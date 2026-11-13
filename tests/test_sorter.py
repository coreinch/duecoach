"""The background pass that files what the user said they need to do, and the coach telling them once."""

import datetime as dt
import json
import time
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from duecoach import chat, db, llm, tools

TZ = ZoneInfo("Europe/Athens")
NOW = dt.datetime(2026, 11, 13, 8, 0, tzinfo=TZ)  # a Friday


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW.timestamp())


@pytest.fixture
def sorter(monkeypatch):
    """Script what the sorting model answers (a dict becomes JSON); every prompt it was given is kept."""
    answers, prompts = [], []

    async def fake_complete(messages, max_tokens=1500, model=None):
        prompts.append(messages[0]["content"])
        answer = answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer)

    monkeypatch.setattr(llm, "_complete", fake_complete)
    return NS(answers=answers, prompts=prompts)


def talk(uid, *pairs):
    for role, text in pairs:
        db.add_message(uid, role, text)


async def test_it_files_tasks_projects_and_appointments_marked_as_filed_by_itself(sorter, user):
    uid = user["user_id"]
    talk(uid, ("user", "I need to renew my passport by the 27th, and I'm moving flat next month. Dentist tomorrow at 14:30"))
    sorter.answers.append(
        {
            "tasks": [{"text": "Renew the passport", "due": "2026-11-27", "important": True, "project_id": None}],
            "projects": [{"title": "Move to the new flat", "steps": ["Look up three moving companies", "Ask each for a quote"]}],
            "events": [{"title": "Dentist", "at": "2026-11-14 14:30"}],
        }
    )
    assert await llm.sort_into_planner(uid) == 3
    tasks = {t["text"]: t for t in db.open_tasks(uid)}
    assert tasks["Renew the passport"]["due"] == "2026-11-27" and tasks["Renew the passport"]["priority"] == 1
    assert tasks["Look up three moving companies"]["project_id"] == 1
    assert [p["title"] for p in db.active_projects(uid)] == ["Move to the new flat"]
    assert db.events_between(uid, 0, 2e10)[0]["title"] == "Dentist"
    fresh = db.unannounced_auto(uid)
    assert len(fresh["tasks"]) == 3 and len(fresh["projects"]) == 1 and len(fresh["events"]) == 1


async def test_nothing_it_files_is_marked_unless_the_sorter_did_it(sorter, user):
    uid = user["user_id"]
    tools.run_tool(uid, "add_task", json.dumps({"text": "Call the bank"}))
    assert db.unannounced_auto(uid)["tasks"] == []
    talk(uid, ("user", "also email Sam"))
    sorter.answers.append({"tasks": [{"text": "Email Sam"}], "projects": [], "events": []})
    await llm.sort_into_planner(uid)
    assert [t["text"] for t in db.unannounced_auto(uid)["tasks"]] == ["Email Sam"]


async def test_it_never_files_something_twice_or_something_invalid(sorter, user):
    uid = user["user_id"]
    tools.run_tool(uid, "add_task", json.dumps({"text": "Call the bank"}))
    talk(uid, ("user", "I should call the bank, and renew the car insurance"))
    sorter.answers.append(
        {
            "tasks": [
                {"text": "call the bank"},  # already there
                {"text": "Renew the car insurance", "due": "friday"},  # not a day: refused
                {"text": "Pay the rent", "due": "2026-11-01"},  # already past: refused
                {"text": "Stray", "project_id": 9},  # no such project: refused
                "not even an object",
            ],
            "projects": [{"title": "Empty", "steps": []}],
            "events": [{"title": "Lunch", "at": "2026-11-14"}, {"title": "Late", "at": "2026-11-13 07:00"}],
        }
    )
    assert await llm.sort_into_planner(uid) == 0
    assert [t["text"] for t in db.open_tasks(uid)] == ["Call the bank"]
    assert db.active_projects(uid) == [] and db.events_between(uid, 0, 2e10) == []


async def test_the_limits_per_pass_hold(sorter, user):
    uid = user["user_id"]
    db.set_field(uid, "planner_sorted", 1)
    talk(uid, ("user", "a long list"))
    chores = ["Water the plants", "Call the bank", "Book a haircut", "Email the landlord", "Renew the passport", "Clean the oven"]
    sorter.answers.append({"tasks": [{"text": c} for c in chores], "projects": [], "events": []})
    assert await llm.sort_into_planner(uid) == 4


async def test_it_adds_a_task_to_an_existing_project(sorter, user):
    uid = user["user_id"]
    tools.run_tool(uid, "add_project", json.dumps({"title": "Trip to Rome", "steps": ["Pick the dates"]}))
    talk(uid, ("user", "I also have to book the flights for Rome"))
    sorter.answers.append({"tasks": [{"text": "Book the flights", "project_id": 1}], "projects": [], "events": []})
    await llm.sort_into_planner(uid)
    assert {t["text"]: t["project_id"] for t in db.open_tasks(uid)}["Book the flights"] == 1
    assert "#1 Trip to Rome" in sorter.prompts[0]  # it was shown what already exists


async def test_the_first_pass_reads_everything_once_and_later_passes_only_the_latest_exchange(sorter, user):
    uid = user["user_id"]
    db.set_field(uid, "notes", "- keeps putting off the taxes")
    db.park_idea(uid, "look up flights")
    for i in range(30):
        talk(uid, ("user", f"message {i}"), ("assistant", f"reply {i}"))
    sorter.answers += [{"tasks": [{"text": "Do the taxes"}]}, {"tasks": []}]
    await llm.sort_into_planner(uid)
    first = sorter.prompts[0]
    assert "first pass over everything" in first and "keeps putting off the taxes" in first and "look up flights" in first
    assert "message 5" in first  # well beyond the last few messages
    assert db.get_user(uid)["planner_sorted"] == 1
    await llm.sort_into_planner(uid)
    later = sorter.prompts[1]
    assert "latest exchange" in later and "message 5" not in later and "keeps putting off" not in later


async def test_an_unreadable_answer_files_nothing_and_the_first_pass_is_tried_again(sorter, user):
    uid = user["user_id"]
    talk(uid, ("user", "I need to email the landlord"))
    sorter.answers += ["I am sorry, I cannot do that", {"tasks": [{"text": "Email the landlord"}]}]
    assert await llm.sort_into_planner(uid) == 0 and db.get_user(uid)["planner_sorted"] == 0
    assert await llm.sort_into_planner(uid) == 1 and db.get_user(uid)["planner_sorted"] == 1


async def test_without_consent_or_a_message_from_the_user_it_does_nothing(sorter, user):
    uid = user["user_id"]
    assert await llm.sort_into_planner(uid) == 0 and sorter.prompts == []  # nothing written yet
    other = db.get_or_create_user("telegram", "2002", "2002", "en")["user_id"]
    talk(other, ("user", "I need to do my taxes"))
    assert await llm.sort_into_planner(other) == 0 and sorter.prompts == []  # has not agreed to the privacy notice


async def test_it_runs_after_a_plain_message_but_not_after_commands_or_check_ins(monkeypatch, user):
    uid = user["user_id"]
    ran = []

    async def fake_reply(u, text, instruction=None):
        return "ok"

    async def fake_sort(u):
        ran.append(u)

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "sort_into_planner", fake_sort)
    await chat.coach(uid, "/stuck the taxes", "steer")
    await chat.coach(uid, "", "a check-in")
    await chat.coach(uid, "/plan")
    assert ran == []
    await chat.coach(uid, "I need to do my taxes")
    for task in list(chat.background):
        await task
    assert ran == [uid]


async def test_the_coach_is_told_once_what_was_filed_then_it_is_marked_as_mentioned(monkeypatch, user):
    uid = user["user_id"]
    db.set_field(uid, "planner_sorted", 1)
    token = db.filing_by_itself.set(True)
    tools.run_tool(uid, "add_task", json.dumps({"text": "Email the landlord", "due": "2026-11-20"}))
    db.filing_by_itself.reset(token)
    seen = []

    async def fake_chat(messages, tool_defs=None, max_tokens=1500, model=None, effort=None):
        seen.append(messages[0]["content"])
        return NS(content="I put the landlord email on your list.", tool_calls=None)

    monkeypatch.setattr(llm, "_chat", fake_chat)
    await llm.reply(uid, "hi")
    assert "Filed for them by yourself" in seen[0] and "Email the landlord, due 2026-11-20" in seen[0]
    await llm.reply(uid, "thanks")
    assert "Filed for them by yourself" not in seen[1]
    assert db.unannounced_auto(uid)["tasks"] == []
    assert [t["text"] for t in db.open_tasks(uid)] == ["Email the landlord"]  # still on the list, just no longer new


async def test_something_filed_while_the_coach_was_writing_is_still_announced_next_time(monkeypatch, user):
    uid = user["user_id"]

    async def fake_chat(messages, tool_defs=None, max_tokens=1500, model=None, effort=None):
        token = db.filing_by_itself.set(True)  # the sorter finishes during this reply
        monkeypatch.setattr(time, "time", lambda: NOW.timestamp() + 5)
        tools.run_tool(uid, "add_task", json.dumps({"text": "Email Sam"}))
        db.filing_by_itself.reset(token)
        return NS(content="Fine.", tool_calls=None)

    monkeypatch.setattr(llm, "_chat", fake_chat)
    await llm.reply(uid, "hi")
    assert [t["text"] for t in db.unannounced_auto(uid)["tasks"]] == ["Email Sam"]
