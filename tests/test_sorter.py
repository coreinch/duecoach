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
    answers, prompts, models = [], [], []

    async def fake_complete(messages, max_tokens=1500, model=None):
        prompts.append(messages[0]["content"])
        models.append(model)
        answer = answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer)

    monkeypatch.setattr(llm, "_complete", fake_complete)
    return NS(answers=answers, prompts=prompts, models=models)


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
    db.set_field(uid, "planner_sorted", llm.SORT_VERSION)
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
    assert db.get_user(uid)["planner_sorted"] == llm.SORT_VERSION
    await llm.sort_into_planner(uid)
    later = sorter.prompts[1]
    assert "latest exchange" in later and "message 5" not in later and "keeps putting off" not in later


async def test_an_unreadable_answer_files_nothing_and_the_first_pass_is_tried_again(sorter, user):
    uid = user["user_id"]
    talk(uid, ("user", "I need to email the landlord"))
    sorter.answers += ["I am sorry, I cannot do that", {"tasks": [{"text": "Email the landlord"}]}]
    assert await llm.sort_into_planner(uid) == 0 and db.get_user(uid)["planner_sorted"] == 0
    assert await llm.sort_into_planner(uid) == 1 and db.get_user(uid)["planner_sorted"] == llm.SORT_VERSION


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
    db.set_field(uid, "planner_sorted", llm.SORT_VERSION)
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


async def test_users_sorted_by_an_older_version_get_one_more_full_pass(sorter, user):
    uid = user["user_id"]
    db.set_field(uid, "planner_sorted", llm.SORT_VERSION - 1)
    for i in range(80):
        talk(uid, ("user", f"message {i}"))
    tools.run_tool(uid, "add_task", json.dumps({"text": "Call the bank"}))
    sorter.answers.append({"tasks": [{"text": "Call the bank"}], "projects": [{"title": "Job search", "steps": ["Update the CV"]}]})
    assert await llm.sort_into_planner(uid) == 1  # the bank task is already there; the project is new
    assert "first pass over everything" in sorter.prompts[0] and "ongoing efforts" in sorter.prompts[0]
    assert "message 5" in sorter.prompts[0]  # reads far back, not only the last few dozen messages
    assert db.get_user(uid)["planner_sorted"] == llm.SORT_VERSION
    talk(uid, ("user", "thanks"))
    sorter.answers.append({})
    await llm.sort_into_planner(uid)
    assert "ongoing efforts" not in sorter.prompts[1]  # an up-to-date user only gets the latest exchange


async def test_the_pass_logs_what_the_model_found_and_what_was_filed_without_any_text(sorter, user, caplog):
    uid = user["user_id"]
    talk(uid, ("user", "my private plan about the divorce lawyer"))
    sorter.answers.append({"tasks": [{"text": "Call the divorce lawyer"}], "projects": [], "events": []})
    with caplog.at_level("INFO", logger="duecoach.llm"):
        await llm.sort_into_planner(uid)
    assert "the model found {'tasks': 1, 'projects': 0, 'events': 0}, filed 1" in caplog.text
    assert "divorce" not in caplog.text


async def test_to_dos_about_the_same_thing_become_one_project(sorter, user):
    uid = user["user_id"]
    first = db.add_task(uid, "Install RAM in the build machine")
    second = db.add_task(uid, "Pick the build machine up after the NVMe arrives")
    talk(uid, ("user", "the build machine needs RAM and an NVMe before I can continue"))
    sorter.answers.append({"projects": [{"title": "Build machine", "steps": [], "task_ids": [first, second]}]})
    assert await llm.sort_into_planner(uid) == 1
    assert [t["project_id"] for t in db.open_tasks(uid)] == [1, 1]
    assert [p["auto"] for p in db.active_projects(uid)] == [1]
    assert f"#{first} Install RAM" in sorter.prompts[0] and "task_ids" in sorter.prompts[0]


async def test_the_one_time_first_pass_uses_the_main_model_and_later_passes_the_side_model(sorter, user):
    uid = user["user_id"]
    talk(uid, ("user", "I need to email the landlord"))
    sorter.answers += [{}, {}]
    await llm.sort_into_planner(uid)
    await llm.sort_into_planner(uid)
    assert sorter.models == [llm.LLM_MODEL, None]  # None: the side model


# --- grouping loose to-dos into projects, and the views catching up before they answer ---


def loose_tasks(uid, *texts):
    return [db.add_task(uid, t) for t in texts]


async def test_to_dos_about_the_same_thing_are_grouped_by_the_main_model(sorter, user):
    uid = user["user_id"]
    ram, nvme, milk = loose_tasks(uid, "Install RAM in yozora", "Pick up yozora after the NVMe arrives", "Buy milk")
    sorter.answers.append({"new_projects": [{"title": "yozora", "task_ids": [ram, nvme]}], "add_to": []})
    assert await llm.group_into_projects(uid) == 1
    assert {t["id"]: t["project_id"] for t in db.open_tasks(uid)} == {ram: 1, nvme: 1, milk: 0}
    assert sorter.models == [llm.LLM_MODEL]
    assert [p["auto"] for p in db.active_projects(uid)] == [1]  # announced to the user at the next reply
    assert f"#{ram} Install RAM" in sorter.prompts[0] and "Buy milk" in sorter.prompts[0]


async def test_loose_to_dos_can_join_a_project_that_exists(sorter, user):
    uid = user["user_id"]
    tools.run_tool(uid, "add_project", json.dumps({"title": "Trip to Rome", "steps": ["Pick the dates"]}))
    flights, hotel = loose_tasks(uid, "Book the flights to Rome", "Reserve a hotel")
    sorter.answers.append(
        {"new_projects": [], "add_to": [{"project_id": 1, "task_ids": [flights]}, {"project_id": 7, "task_ids": [hotel]}]}
    )
    assert await llm.group_into_projects(uid) == 1
    assert {t["text"]: t["project_id"] for t in db.open_tasks(uid)}["Book the flights to Rome"] == 1
    assert {t["text"]: t["project_id"] for t in db.open_tasks(uid)}["Reserve a hotel"] == 0  # no such project: left alone


async def test_it_asks_again_only_when_the_loose_to_dos_changed(sorter, user):
    uid = user["user_id"]
    loose_tasks(uid, "Water the plants", "Call the bank")
    sorter.answers.append({"new_projects": [], "add_to": []})
    assert await llm.group_into_projects(uid) == 0 and len(sorter.prompts) == 1
    assert await llm.group_into_projects(uid) == 0 and len(sorter.prompts) == 1  # same to-dos: no second call
    loose_tasks(uid, "Book a haircut")
    sorter.answers.append({"new_projects": [], "add_to": []})
    await llm.group_into_projects(uid)
    assert len(sorter.prompts) == 2


async def test_one_loose_to_do_or_nothing_valid_makes_no_project_and_no_call(sorter, user):
    uid = user["user_id"]
    loose_tasks(uid, "Call the bank")
    assert await llm.group_into_projects(uid) == 0 and sorter.prompts == []
    loose_tasks(uid, "Water the plants", "Book a haircut")
    sorter.answers.append(
        {"new_projects": [{"title": "Odd", "task_ids": [1, 99]}, {"title": "", "task_ids": [2, 3]}, "junk"], "add_to": "junk"}
    )
    assert await llm.group_into_projects(uid) == 0
    assert db.active_projects(uid) == []


async def test_an_unreadable_grouping_answer_is_tried_again_next_time(sorter, user):
    uid = user["user_id"]
    loose_tasks(uid, "Water the plants", "Call the bank")
    sorter.answers += ["no json here", {"new_projects": [], "add_to": []}]
    await llm.group_into_projects(uid)
    await llm.group_into_projects(uid)
    assert len(sorter.prompts) == 2


async def test_grouping_runs_after_the_sort_for_a_plain_message(monkeypatch, user):
    uid = user["user_id"]
    order = []

    async def fake_reply(u, text, instruction=None):
        return "ok"

    async def fake_sort(u):
        order.append("sort")

    async def fake_group(u):
        order.append("group")

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "sort_into_planner", fake_sort)
    monkeypatch.setattr(llm, "group_into_projects", fake_group)
    await chat.coach(uid, "I need to install the RAM")
    for task in list(chat.background):
        await task
    assert order == ["sort", "group"]


async def test_projects_and_tasks_catch_up_before_they_answer(sorter, user):
    from duecoach import commands

    uid = user["user_id"]
    talk(uid, ("user", "I keep working on yozora, my Linux distro"))
    ram, nvme = loose_tasks(uid, "Install RAM in yozora", "Pick up yozora after the NVMe arrives")
    # the first pass (nothing new), then the grouping
    sorter.answers += [{}, {"new_projects": [{"title": "yozora", "task_ids": [ram, nvme]}], "add_to": []}]
    view = await commands.COMMANDS["projects"](uid, [])
    assert view == "Your projects:\n• yozora — next: Install RAM in yozora (2 open)"
    assert db.get_user(uid)["planner_sorted"] == llm.SORT_VERSION
    # a second look needs no model call: nothing has changed
    assert await commands.COMMANDS["projects"](uid, []) == view and len(sorter.prompts) == 2


async def test_the_views_still_answer_when_the_model_is_down(user):
    from duecoach import commands

    uid = user["user_id"]
    loose_tasks(uid, "Call the bank", "Water the plants")  # conftest makes the real model refuse every call
    assert "Call the bank" in await commands.COMMANDS["tasks"](uid, [])
    assert "No projects yet" in await commands.COMMANDS["projects"](uid, [])


async def test_short_acknowledgements_are_not_sorted(monkeypatch, user):
    uid = user["user_id"]
    ran = []

    async def fake_reply(u, text, instruction=None):
        return "ok"

    async def fake_sort(u):
        ran.append(u)

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "sort_into_planner", fake_sort)
    for text in ("ok", "yes please", "thanks"):
        await chat.coach(uid, text)
    for task in list(chat.background):
        await task
    assert ran == []


async def test_a_burst_of_messages_is_sorted_by_one_run_at_the_start_and_one_at_the_end(monkeypatch, user):
    uid = user["user_id"]
    ran = []

    async def fake_reply(u, text, instruction=None):
        return "ok"

    async def fake_sort(u):
        ran.append(u)

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "sort_into_planner", fake_sort)
    monkeypatch.setattr(chat, "SORT_COOLDOWN", 0.05)
    for text in ("I need to call the bank", "and email the landlord", "also renew my passport soon", "plus pay the rent"):
        await chat.coach(uid, text)
    for task in list(chat.background):
        await task
    assert len(ran) == 2  # four messages, two model calls: the first at once, one more when the cooldown ended
