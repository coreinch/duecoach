import json
import time

import pytest

from coach import bot, channels, core, db, flows, llm, prompts
from coach.flows import goals


@pytest.fixture
def model(monkeypatch):
    """The model is replaced: drafts follow a script, coaching replies record what they were asked."""
    log = {"coach": [], "drafts": []}

    async def fake_reply(uid, text, instruction=None):
        log["coach"].append((text, instruction))
        return "coached"

    async def fake_draft(uid, kind, text, previous=None, goal=""):
        log["drafts"].append((kind, text, previous))
        if text.lower().startswith("thanks"):
            return None
        return f"{kind}: {text}" if previous is None else f"{kind} (revised): {text}"

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "draft", fake_draft)
    return log


@pytest.fixture
def person(model):
    """Agreed, timezone confirmed, three exchanges of chat so far (so the count-based fallback applies), no goals yet."""
    row = db.get_or_create_user("telegram", "4004", "4004", "en")
    db.set_fields(row["user_id"], consent_at=1.0, tz_set=1, intake_state="done")
    for i in range(6):
        db.add_message(row["user_id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
    return row["user_id"]


async def say(text, ext="4004"):
    return await core.handle_text("telegram", ext, ext, text)


def seed(uid, n):
    for i in range(n):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"filler {i}")


def age_objectives(uid, days):
    db._conn.execute("UPDATE objectives SET created=? WHERE user_id=?", (time.time() - days * 86400, uid))
    db._conn.commit()


async def test_a_goal_and_a_first_step_are_set_up_in_one_conversation(person):
    reply = await say("hello")
    assert reply.startswith("coached") and "set a goal together" in reply
    assert "Here's how I'd write that as a goal" in await say("I want to stop being late for work")
    saved = await say("yes")
    assert 'Goal saved: "goal: I want to stop being late for work"' in saved and "small step toward" in saved
    assert [g["text"] for g in db.active_goals(person)] == ["goal: I want to stop being late for work"]
    assert "I'd write the step like this" in await say("lay out my clothes at 9pm tonight")
    assert "small reward" in await say("yes")
    done = await say("a good coffee")
    assert "Saved your step for this week" in done and "(reward: a good coffee)" in done
    (step,) = db.open_objectives(person)
    assert step["text"] == "objective: lay out my clothes at 9pm tonight" and step["incentive"] == "a good coffee"
    assert flows.get(person) is None and await say("hello again") == "coached"  # nothing left pending, nothing re-asked


async def test_the_draft_can_be_revised_and_a_plain_no_asks_what_to_change(person):
    await say("hello")
    await say("get my finances in order")
    assert "(revised)" in await say("make it about paying bills weekly")
    assert "What would you change?" in await say("no")
    assert "(revised)" in await say("only on Sundays")
    await say("yes")
    assert db.active_goals(person)[0]["text"].startswith("goal (revised)")


async def test_after_a_few_revisions_the_current_draft_is_kept(person):
    await say("hello")
    await say("be calmer")
    for i in range(goals.MAX_REVISIONS):
        assert "Here's how" in await say(f"change number {i}")
    assert "Goal saved" in await say("one more change please")
    assert len(db.active_goals(person)) == 1


async def test_skipping_stops_the_questions_for_a_while(person):
    await say("hello")
    assert "Say /goal whenever" in await say("skip")
    for _ in range(3):
        assert await say("just chatting") == "coached"
    assert flows.get(person) is None and db.active_goals(person) == []
    assert "set a goal together" in await say("/goal")  # but the user can still start it themselves


async def test_vague_answers_are_asked_about_twice_then_treated_as_chat(person):
    await say("hello")
    assert "a little more" in await say("thanks")
    assert "a little more" in await say("thanks a lot")
    assert await say("thanks again") == "coached"
    assert flows.get(person) is None


async def test_a_step_that_is_a_few_days_old_is_followed_up_and_the_result_is_coached(person, model):
    db.add_goal(person, "g")
    db.add_objective(person, None, "lay out clothes", "coffee")
    age_objectives(person, 3)
    assert "How did this go" in await say("hi")
    assert "What got in the way" in await say("2")
    assert await say("1") == "coached"
    (step,) = db._conn.execute("SELECT status, barrier FROM objectives").fetchall()
    assert (step["status"], step["barrier"]) == ("partial", "forgot")
    text, instruction = model["coach"][-1]
    assert "partly done" in instruction and "forgot" in instruction and "lay out clothes" in instruction


async def test_a_finished_step_is_recorded_and_coached_for_what_worked(person, model):
    db.add_goal(person, "g")
    db.add_objective(person, None, "walk daily", "")
    age_objectives(person, 3)
    await say("hi")
    assert await say("1") == "coached"
    assert db._conn.execute("SELECT status FROM objectives").fetchone()["status"] == "done"
    assert "finished this weekly step" in model["coach"][-1][1]
    seed(person, flows.QUESTION_GAP)  # a couple of exchanges since the bot last asked something
    assert "small step toward" in await say("ok what now")  # next: a new step, since none is open


async def test_the_followup_free_text_barrier_is_kept_and_unknown_replies_are_asked_again(person):
    db.add_goal(person, "g")
    db.add_objective(person, None, "tidy desk", "")
    age_objectives(person, 3)
    await say("hi")
    assert "Please reply 1" in await say("hmm")
    await say("3")
    await say("my cat sat on the keyboard all week")
    row = db._conn.execute("SELECT status, barrier, note FROM objectives").fetchone()
    assert (row["status"], row["barrier"], row["note"]) == ("missed", "other", "my cat sat on the keyboard all week")


async def test_not_now_keeps_the_step_open_and_waits_before_asking_again(person):
    db.add_goal(person, "g")
    db.add_objective(person, None, "tidy desk", "")
    age_objectives(person, 3)
    await say("hi")
    assert "come back to it" in await say("later")
    assert len(db.open_objectives(person)) == 1
    assert "How did this go" not in await say("hi again")


async def test_goal_limits_and_step_limits_are_explained(person):
    for i in range(db.MAX_GOALS):
        db.add_goal(person, f"g{i}")
    assert "maximum of 4 goals" in await say("/goal")
    for i in range(db.MAX_OPEN_OBJECTIVES):
        db.add_objective(person, None, f"s{i}", "")
    assert "already have 3 open steps" in await say("/step")


async def test_step_without_a_goal_starts_with_the_goal(person):
    assert "set a goal together" in await say("/step")


async def test_only_one_question_at_a_time_goal_before_timezone(person):
    db.set_field(person, "tz_set", 0)
    for i in range(2):
        db.add_message(person, "user", f"x{i}")  # now six messages: both questions would be due
    reply = await say("hello")
    assert "set a goal together" in reply and "where do you live" not in reply  # the goal question first, alone
    after_skip = await say("skip")
    assert "Say /goal whenever" in after_skip and "where do you live" in after_skip  # and the timezone question straight after it


async def test_an_unanswered_question_expires_after_a_day(person):
    await say("hello")
    state = json.loads(db.get_user(person)["flow_state"])
    state["started"] = time.time() - flows.FLOW_TTL - 10
    db.set_field(person, "flow_state", json.dumps(state))
    assert flows.get(person) is None and db.get_user(person)["flow_state"] == ""


async def test_commands_do_not_answer_a_pending_question(person):
    await say("hello")
    assert "/stuck" in await say("/help")
    assert flows.get(person)["step"] == "asked"


async def test_checkins_wait_while_a_question_is_pending_and_use_the_fixed_followup_when_a_step_is_due(person, monkeypatch):
    sent = []

    class Fake:
        name = "telegram"

        def can_send(self, u, template_ok=True):
            return True

        async def send(self, u, text):
            sent.append(text)

    monkeypatch.setitem(channels.REGISTRY, "telegram", Fake())
    db.set_fields(person, interval_min=30, last_inbound=0, last_proactive=0)
    db.add_goal(person, "g")
    db.add_objective(person, None, "tidy desk", "")
    age_objectives(person, 3)
    flows.put(person, "goal", "asked")
    assert bot.plan_checkin(db.get_user(person), time.time(), _midday()) is None  # a question is already waiting
    flows.clear(person)
    await bot.checkin(_make_due(person))
    assert sent and "How did this go" in sent[0]
    assert flows.get(person)["flow"] == "followup"


def _midday():
    import datetime as dt
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("Europe/Athens")).replace(hour=12, minute=0)


def _make_due(uid):
    """Open the daily window and the interval gate no matter what time the test runs."""
    db.set_fields(uid, morning_hour=0, evening_hour=23, last_morning=_midday().date().isoformat(), tz="Europe/Athens")
    return db.get_user(uid)


async def test_draft_helper_falls_back_to_the_users_words_and_recognises_non_goals(person, monkeypatch):
    calls = iter(["Arrive on time 4 days a week for a month", "NONE", "None of my work gets done, so I'll plan 30 minutes a day", ""])

    async def scripted(messages, max_tokens=1500):
        return next(calls)

    monkeypatch.setattr(llm, "_complete", scripted)
    monkeypatch.undo()  # use the real draft(), with only the model call scripted
    monkeypatch.setattr(llm, "_complete", scripted)
    assert await llm.draft(person, "goal", "I'm always late") == "Arrive on time 4 days a week for a month"
    assert await llm.draft(person, "goal", "thanks!") is None
    assert (await llm.draft(person, "goal", "x y")).startswith("None of my work")  # a real goal that starts with "None"
    assert await llm.draft(person, "goal", "improve my sleep habits") == "improve my sleep habits"  # empty answer: their own words

    async def failing(messages, max_tokens=1500):
        raise RuntimeError("down")

    monkeypatch.setattr(llm, "_complete", failing)
    assert await llm.draft(person, "objective", "call the dentist on Monday", goal="g") == "call the dentist on Monday"
    assert prompts.DRAFT_GOAL and prompts.DRAFT_OBJECTIVE
