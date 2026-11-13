"""Tasks with due days, the calendar, notes, and the agenda the check-ins are handed."""

import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

import pytest

from duecoach import agenda, bot, channels, commands, db, llm, prompts, tools

TZ = ZoneInfo("Europe/Athens")
NOW = dt.datetime(2026, 11, 13, 8, 0, tzinfo=TZ)  # a Friday


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW.timestamp())


def at(day: str, hh_mm: str) -> float:
    return dt.datetime.fromisoformat(f"{day} {hh_mm}").replace(tzinfo=TZ).timestamp()


def run(uid, tool, **args):
    return tools.run_tool(uid, tool, json.dumps(args))


async def cmd(uid, line):
    name, *args = line.lstrip("/").split()
    return await commands.COMMANDS[name](uid, args)


def test_day_words():
    today = NOW.date()
    assert agenda.parse_day("today", today) == today
    assert agenda.parse_day("tomorrow", today) == dt.date(2026, 11, 14)
    assert agenda.parse_day("fri", today) == dt.date(2026, 11, 20)  # never today: the next one
    assert agenda.parse_day("mon", today) == dt.date(2026, 11, 16)
    assert agenda.parse_day("αύριο", today) == dt.date(2026, 11, 14)
    assert agenda.parse_day("2026-12-01", today) == dt.date(2026, 12, 1)
    assert agenda.parse_day("20/11", today) == dt.date(2026, 11, 20)
    assert agenda.parse_day("1/11", today) == dt.date(2027, 11, 1)  # already past this year
    assert agenda.parse_day("31/2", today) is None
    assert agenda.parse_day("dentist", today) is None


async def test_tasks_are_listed_dated_first_and_overdue_is_flagged(user):
    uid = user["user_id"]
    db.add_task(uid, "someday thing")
    db.add_task(uid, "later", "2026-11-20")
    db.add_task(uid, "missed", "2026-11-10")
    db.add_task(uid, "today one", "2026-11-13", 1)
    lines = (await cmd(uid, "/tasks")).splitlines()[2:]
    assert [line.split(" ", 2)[2].split(" —")[0] for line in lines] == ["missed", "★ today one", "later", "someday thing"]
    assert "overdue" in lines[0] and "today" in lines[1]


async def test_today_shows_the_day_and_calendar_the_week(user):
    uid = user["user_id"]
    db.add_event(uid, "Standup", at("2026-11-13", "10:00"))
    db.add_event(uid, "Dentist", at("2026-11-16", "09:30"))
    db.add_event(uid, "Far away", at("2026-12-30", "09:30"))
    db.add_reminder(uid, at("2026-11-13", "13:00"), "take the pill")
    db.add_task(uid, "send the invoice", "2026-11-13")
    db.add_task(uid, "missed", "2026-11-12")
    today = await cmd(uid, "/today")
    assert "Fri 13 Nov" in today and "10:00 Standup" in today and "13:00 take the pill" in today
    assert "send the invoice" in today and "overdue" in today and "Dentist" not in today
    week = await cmd(uid, "/calendar")
    assert "Mon 16 Nov:\n• 09:30 Dentist" in week and "Far away" not in week and "send the invoice" in week


async def test_an_empty_day_says_so(user):
    assert "Nothing planned" in await cmd(user["user_id"], "/today")
    assert "Nothing planned" in await cmd(user["user_id"], "/calendar")


async def test_notes_command_lists_only_the_users_own_notes_and_memory_is_separate(user):
    uid = user["user_id"]
    other = db.get_or_create_user("telegram", "2002", "2002", "en")["user_id"]
    assert "No notes" in await cmd(uid, "/notes")
    db.add_memo(uid, "locker code 4821")
    db.add_memo(uid, "plumber 555-0101")
    db.add_memo(other, "not yours")
    db.set_field(uid, "notes", "likes timers")
    listing = await cmd(uid, "/notes")
    assert listing.index("plumber") < listing.index("locker") and "not yours" not in listing and "likes timers" not in listing
    assert "likes timers" in await cmd(uid, "/memory")


def test_the_arguments_commands_are_gone():
    for name in ("add", "done", "drop", "event", "note"):
        assert name not in commands.COMMANDS


def test_note_and_task_tools_for_the_coach(user):
    uid = user["user_id"]
    assert run(uid, "add_note", text="locker code 4821").startswith("ok: note #")
    assert "already noted" in run(uid, "add_note", text="Locker code 4821")
    assert "ok: note deleted" == run(uid, "delete_note", note_id=db.memos(uid)[0]["id"])
    assert "no such note" in run(uid, "delete_note", note_id=99)

    assert "due 2026-11-20" in run(uid, "add_task", text="renew the passport", due="2026-11-20", important=True)
    assert db.open_tasks(uid)[0]["priority"] == 1
    assert run(uid, "add_task", text="renew passport", due="2026-11-27").endswith("its due day is now 2026-11-27")
    assert run(uid, "add_task", text="x", due="2026-11-01").startswith("error: `due`")  # already past
    assert run(uid, "add_task", text="x", due="friday").startswith("error: `due`")
    tid = db.open_tasks(uid)[0]["id"]
    assert run(uid, "update_task", task_id=tid, due="").startswith("ok")
    assert db.open_tasks(uid)[0]["due"] == ""
    assert run(uid, "update_task", task_id=tid, important=False).startswith("ok") and db.open_tasks(uid)[0]["priority"] == 0
    assert run(uid, "update_task", task_id=tid).startswith("error: nothing to change")
    assert run(uid, "update_task", task_id=tid, text="renew it online") == "ok: updated"


def test_the_coach_sees_due_days_importance_and_notes(user):
    uid = user["user_id"]
    db.add_task(uid, "send the invoice", "2026-11-12", 1)
    db.add_memo(uid, "locker code 4821")
    state = tools.coaching_state(uid)
    assert "#1 send the invoice, due overdue (Thu 12 Nov), important" in state
    assert "locker code 4821" in state


def test_morning_block_has_the_day_and_nothing_else_when_empty(user):
    uid = user["user_id"]
    assert agenda.prompt_block(uid, "morning") == ""
    db.add_event(uid, "Standup", at("2026-11-13", "10:00"))
    db.add_event(uid, "Yesterday's", at("2026-11-12", "10:00"))
    db.add_task(uid, "send the invoice", "2026-11-13")
    db.add_task(uid, "missed", "2026-11-12")
    db.add_task(uid, "big one", "", 1)
    block = agenda.prompt_block(uid, "morning")
    assert "10:00 Standup" in block and "Yesterday" not in block
    assert "Due today: #1 send the invoice" in block and "Overdue" in block and "(top priority) big one" in block


def test_evening_block_looks_at_tomorrow_and_what_got_done(user):
    uid = user["user_id"]
    db.add_event(uid, "Interview", at("2026-11-14", "09:00"))
    db.add_task(uid, "prep slides", "2026-11-14")
    done = db.add_task(uid, "email Sam")
    db.close_task(uid, done, "done")
    block = agenda.prompt_block(uid, "evening")
    assert "Tomorrow: 09:00 Interview" in block and "Due tomorrow: #1 prep slides" in block and "email Sam" in block


def test_pulse_block_only_has_what_is_close(user):
    uid = user["user_id"]
    db.add_event(uid, "Call", at("2026-11-13", "10:00"))
    db.add_event(uid, "Dinner", at("2026-11-13", "20:00"))
    block = agenda.prompt_block(uid, "pulse")
    assert "Call" in block and "Dinner" not in block


async def test_check_ins_are_handed_the_agenda(user, monkeypatch):
    uid = user["user_id"]
    db.set_fields(uid, interval_min=30, tz="Europe/Athens", tz_set=1, last_inbound=time.time() - 3 * 86400)
    db.add_event(uid, "Standup", at("2026-11-13", "10:00"))
    seen = []

    async def fake_reply(u, text, instruction=None):
        seen.append(instruction)
        return "good morning"

    class Sink:
        name = "telegram"

        async def send(self, u, text):
            pass

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setitem(channels.REGISTRY, "telegram", Sink())
    await bot.checkin(db.get_user(uid))
    assert seen and seen[0].startswith(prompts.MORNING) and "10:00 Standup" in seen[0]


def test_events_are_saved_by_the_coach_in_local_time(user):
    uid = user["user_id"]
    assert run(uid, "add_event", title="dentist", at="2026-11-14 14:30").startswith("ok: event #1")
    assert db.events_between(uid, 0, 2e10)[0]["start"] == at("2026-11-14", "14:30")


def test_breaking_a_task_down_makes_ordered_steps_that_inherit_the_due_day(user):
    uid = user["user_id"]
    tid = db.add_task(uid, "sort out my taxes", "2026-11-27")
    reply = run(uid, "break_down_task", task_id=tid, steps=["Find last year's return", "List the income documents", "Fill in section 1"])
    assert reply.startswith("ok: 3 steps saved under #1")
    steps = [t for t in db.open_tasks(uid) if t["parent_id"] == tid]
    assert [t["text"] for t in steps] == ["Find last year's return", "List the income documents", "Fill in section 1"]
    assert all(t["due"] == "2026-11-27" for t in steps)
    assert "already has 3 steps" in run(uid, "break_down_task", task_id=tid, steps=["a", "b"])
    assert "already a step" in run(uid, "break_down_task", task_id=steps[0]["id"], steps=["a", "b"])
    assert run(uid, "break_down_task", task_id=99, steps=["a", "b"]).startswith("error: no such open")
    assert run(uid, "break_down_task", task_id=tid, steps=["only one"]).startswith("error: give 2 to 7")
    assert run(uid, "break_down_task", task_id=tid, steps="not a list").startswith("error: `steps`")


def test_closing_the_whole_closes_its_steps(user):
    uid = user["user_id"]
    tid = db.add_task(uid, "clean the flat")
    run(uid, "break_down_task", task_id=tid, steps=["Clear the desk", "Vacuum the floor"])
    assert run(uid, "complete_task", task_id=tid) == "ok: ticked off"
    assert db.open_tasks(uid) == []


def test_a_project_starts_with_steps_and_dedupes(user):
    uid = user["user_id"]
    reply = run(uid, "add_project", title="Move to the new flat", steps=["Look up three moving companies", "Ask each for a quote"])
    assert reply.startswith("ok: project #1 saved with 2 steps")
    assert [t["project_id"] for t in db.open_tasks(uid)] == [1, 1]
    assert "already a project as #1" in run(uid, "add_project", title="move to the new flat", steps=["x"])
    assert run(uid, "add_project", title="empty", steps=[]).startswith("error: give 1 to 6")
    # a step added later joins the project, and a made-up project id is refused
    assert run(uid, "add_task", text="Book the van", project_id=1).startswith("ok")
    assert run(uid, "add_task", text="Stray", project_id=7) == "error: no such active project"
    assert [t["project_id"] for t in db.open_tasks(uid)] == [1, 1, 1]


def test_steps_of_a_task_in_a_project_belong_to_the_project(user):
    uid = user["user_id"]
    run(uid, "add_project", title="Trip", steps=["Pick the dates"])
    tid = db.open_tasks(uid)[0]["id"]
    run(uid, "break_down_task", task_id=tid, steps=["Check my calendar", "Ask Sam"])
    assert {t["project_id"] for t in db.open_tasks(uid)} == {1}


def test_a_dropped_project_takes_its_open_to_dos_and_a_done_one_keeps_history(user):
    uid = user["user_id"]
    run(uid, "add_project", title="Trip", steps=["Pick the dates", "Book the flight"])
    run(uid, "add_project", title="Taxes", steps=["Find the forms"])
    assert run(uid, "close_project", project_id=1, outcome="dropped") == "ok: project closed"
    assert [t["text"] for t in db.open_tasks(uid)] == ["Find the forms"]
    assert run(uid, "close_project", project_id=2, outcome="done") == "ok: project closed"
    assert run(uid, "close_project", project_id=2, outcome="done").startswith("error")
    assert run(uid, "close_project", project_id=1, outcome="bogus").startswith("error: outcome")


async def test_tasks_view_groups_by_project_with_steps_and_flags_a_stalled_project(user):
    uid = user["user_id"]
    run(uid, "add_project", title="Trip", steps=["Pick the dates"])
    run(uid, "add_project", title="Taxes", steps=["Find the forms"])
    db.add_task(uid, "buy milk")
    db.close_task(uid, db.open_tasks(uid)[1]["id"], "done")  # finish the only step of "Taxes"
    run(uid, "break_down_task", task_id=db.open_tasks(uid)[0]["id"], steps=["Check my calendar", "Ask Sam"])
    view = await cmd(uid, "/tasks")
    assert "Project: Trip\n• #1 Pick the dates\n    ◦ #4 Check my calendar\n    ◦ #5 Ask Sam" in view
    assert "Project: Taxes\n    (no next step yet)" in view and "Other to-dos:\n• #3 buy milk" in view


def test_the_coach_is_told_which_project_has_no_next_step(user):
    uid = user["user_id"]
    run(uid, "add_project", title="Taxes", steps=["Find the forms"])
    run(uid, "complete_task", task_id=1)
    state = tools.coaching_state(uid)
    assert "Project #1 Taxes: NO OPEN NEXT STEP" in state
    assert "Projects with no open next step: Taxes" in agenda.prompt_block(uid, "morning")
    assert "Projects with no open next step" in agenda.prompt_block(uid, "evening")
    assert "Projects with no open next step" not in agenda.prompt_block(uid, "pulse")


async def test_projects_view_shows_the_next_step_and_flags_a_stalled_project(user):
    uid = user["user_id"]
    assert "No projects yet" in await cmd(uid, "/projects")
    run(uid, "add_project", title="Trip", steps=["Pick the dates", "Book the flight"])
    run(uid, "add_project", title="Taxes", steps=["Find the forms"])
    run(uid, "break_down_task", task_id=1, steps=["Check my calendar", "Ask Sam"])
    run(uid, "complete_task", task_id=3)  # the only step of "Taxes"
    view = await cmd(uid, "/projects")
    assert "• Trip — next: Check my calendar (4 open)" in view  # the first step under the first to-do, not the umbrella
    assert "• Taxes — no next step yet" in view
