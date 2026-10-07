import pytest

from coach import core, db, flows, llm

IDEAS = [
    "Put my keys in one bowl by the door every evening for the next four weeks.",
    "Set out my clothes for work each night, on at least 4 days a week for the next month.",
    "Leave the house 10 minutes earlier than I need to, every workday for the next month.",
]
ANSWERS = ["mornings fall apart", "alarms I ignore", "starting tasks", "I'm creative", "wake 7, sleep 1am", "a bit stressed"]


@pytest.fixture
def model(monkeypatch):
    async def fake_suggest(uid):
        return list(IDEAS)

    async def fake_draft(uid, kind, text, previous=None, goal=""):
        return f"{kind}: {text}"

    async def fake_reply(uid, text, instruction=None, coach=True):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "suggest_goals", fake_suggest)
    monkeypatch.setattr(llm, "draft", fake_draft)
    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "cards_read", lambda uid: set())


async def say(text, ext="6006", lang="en"):
    return await core.handle_text("telegram", ext, ext, text, lang)


def ends_with_something_to_do(reply: str) -> bool:
    """A message must never leave the user wondering what to do: it ends with a question, a choice, or an instruction."""
    last = reply.strip().splitlines()[-1]
    return last.endswith(("?", ".", '"')) and any(
        word in reply.lower() for word in ("reply", "tell me", "say ", "send /", "talk to me", "?", "what ", "pick ")
    )


async def test_the_whole_setup_flows_from_the_first_message_to_normal_conversation(model):
    transcript = []

    async def turn(text):
        reply = await say(text)
        transcript.append((text, reply))
        return reply

    # intro
    assert "AI coaching assistant" in await turn("hi")
    agreed = await turn("/agree")
    assert "a few quick questions" in agreed and "tell me what brought you here" in agreed
    # a first exchange, then the interview begins by itself
    assert await turn("mornings are chaos") == "coached"
    started = await turn("I'd like to fix that")
    assert started.startswith("coached") and "a few quick questions" in started and "what made you look for support" in started
    # the interview (six questions), ending in the coach's reply and the first goal question
    for answer in ANSWERS[:-1]:
        assert (await turn(answer)).startswith("Thanks.")
    goal_question = await turn(ANSWERS[-1])
    assert goal_question.startswith("coached") and "1) " in goal_question and "Reply 1, 2 or 3" in goal_question
    # goal -> first step -> reward
    step_question = await turn("2")
    assert f'Goal saved: "{IDEAS[1]}"' in step_question and "small step toward" in step_question
    assert "I'd write the step like this" in await turn("clothes out at 9pm tonight")
    assert "small reward" in await turn("yes")
    # the step is saved, and the conversation goes straight on to the last setup question
    saved = await turn("a good coffee")
    assert "Saved your step for this week" in saved and "One last thing" in saved and "where do you live" in saved
    # timezone: assumed, local time shown, then the hand-over
    assumed = await turn("Athens")
    assert "where you are right now, right?" in assumed and "30 minutes of quiet" in assumed
    handoff = await turn("yes")
    assert handoff.startswith("Great, thanks.") and "You're all set" in handoff
    assert 'Your step this week: "objective: clothes out at 9pm tonight"' in handoff and "remind you" in handoff
    user = db.get_user(6006)
    assert user["onboarded"] == 1 and flows.get(6006) is None and user["interval_min"] == 30
    # main: ordinary conversation, with nothing left pending and no second hand-over
    assert await turn("I'll do it tomorrow at 9") == "coached"
    assert await turn("thanks") == "coached"
    # no message in the setup left the user without a next move
    for _, reply in transcript[:-2]:
        if reply != "coached":  # (the bare coach reply stands for the model's own answer, which asks its own question)
            assert ends_with_something_to_do(reply), reply


async def test_skipping_the_goal_still_leads_on_to_the_timezone_and_then_the_hand_over(model):
    db.get_or_create_user("telegram", "6006", "6006", "en")
    db.set_fields(6006, consent_at=1.0, intake_state="done")
    for i in range(2):
        db.add_message(6006, "user", f"m{i}")
    assert "set a goal together" in await say("I always forget my keys")
    skipped = await say("skip")
    assert "Say /goal whenever" in skipped and "where do you live" in skipped
    assert "where you are right now, right?" in await say("Athens")
    done = await say("yes")
    assert "You're all set" in done and "Send /goal whenever you want to set a goal" in done


async def test_skipping_the_timezone_ends_the_setup_with_a_hand_over_that_says_how_to_start_check_ins(model):
    uid = db.get_or_create_user("telegram", "6006", "6006", "en")["user_id"]
    db.set_fields(uid, consent_at=1.0, intake_state="done")
    db.add_goal(uid, "a goal")
    db.set_fields(uid, tz_state="asked")
    reply = await say("skip")
    assert (
        "No problem" in reply
        and "You're all set" in reply
        and "Send /step" in reply
        and "Check-ins start once you set your timezone" in reply
    )
    assert db.get_user(uid)["onboarded"] == 1


async def test_with_the_timezone_already_known_the_step_leads_straight_to_the_hand_over(model):
    uid = db.get_or_create_user("telegram", "6006", "6006", "en")["user_id"]
    db.set_fields(uid, consent_at=1.0, tz_set=1, intake_state="done")
    db.add_goal(uid, "a goal")
    flows.put(uid, "objective", "reward", candidate="a step", goal_id=1)
    reply = await say("none")
    assert "Saved your step" in reply and "You're all set" in reply and "where do you live" not in reply


async def test_a_correction_to_the_local_time_also_ends_with_the_hand_over(model):
    uid = db.get_or_create_user("telegram", "6006", "6006", "en")["user_id"]
    db.set_fields(uid, consent_at=1.0, intake_state="done", tz_state="asked")
    db.add_goal(uid, "a goal")
    await say("Athens")
    await say("no")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    reply = await say(datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%H:%M"))
    assert "I've fixed it" in reply and "You're all set" in reply


async def test_the_hand_over_comes_once_and_not_to_people_already_using_the_bot(model):
    uid = db.get_or_create_user("telegram", "6006", "6006", "en")["user_id"]
    db.set_fields(uid, consent_at=1.0, tz_set=1, intake_state="done")
    flows.put(uid, "goal", "asked", options=IDEAS)
    assert "You're all set" in await say("skip")  # setup over: the hand-over
    flows.put(uid, "goal", "asked", options=IDEAS)
    assert "You're all set" not in await say("skip")  # a later /goal that is skipped is just that


def test_migration_marks_people_already_using_the_bot_as_set_up(tmp_path):
    path = str(tmp_path / "old.db")
    db.close()
    db.init(path)
    db._conn.execute("INSERT INTO users (user_id, ext_id, channel, chat_id, tz) VALUES (42, '42', 'telegram', 42, 'Europe/Athens')")
    db._conn.execute("PRAGMA user_version=6")
    db._conn.commit()
    db.close()
    db.init(path)
    assert db.get_user(42)["onboarded"] == 1
    assert db.get_or_create_user("telegram", "43", "43")["onboarded"] == 0


@pytest.mark.parametrize(
    ("goal", "quoted"),
    [
        ("Be on time for work", True),
        ("Leave the house 10 minutes earlier than I need to, on at least 4 days a week for the next month.", False),
    ],
)
async def test_the_step_question_quotes_a_short_goal_but_not_a_long_one_that_was_just_shown(model, goal, quoted):
    uid = db.get_or_create_user("telegram", "6006", "6006", "en")["user_id"]
    db.set_fields(uid, consent_at=1.0, tz_set=1, intake_state="done")
    db.add_goal(uid, goal)
    question = await flows.start_objective(uid)
    assert (f"“{goal}”" in question) is quoted and ("that goal" in question) is not quoted
