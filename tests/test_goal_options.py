import asyncio

import pytest

from coach import core, db, flows, llm

IDEAS = [
    "Put my keys in one bowl by the door every evening for the next four weeks.",
    "Set out my clothes for work each night, on at least 4 days a week for the next month.",
    "Leave the house 10 minutes earlier than I need to, every workday for the next month.",
]


@pytest.fixture
def model(monkeypatch):
    """The model is replaced: it suggests IDEAS, drafts echo the text, and replies are fixed."""
    log = {"drafts": [], "suggested": 0}

    async def fake_suggest(uid):
        log["suggested"] += 1
        return list(IDEAS)

    async def fake_draft(uid, kind, text, previous=None, goal=""):
        log["drafts"].append((kind, text, previous))
        return f"{kind}: {text}" if previous is None else f"{kind} (revised): {text} <- {previous[:20]}"

    async def fake_reply(uid, text, instruction=None, coach=True):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "suggest_goals", fake_suggest)
    monkeypatch.setattr(llm, "draft", fake_draft)
    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "cards_read", lambda uid: set())
    return log


def make_user(lang="en", profile=None, ext="3003"):
    row = db.get_or_create_user("telegram", ext, ext, lang)
    uid = row["user_id"]
    db.set_fields(uid, consent_at=1.0, tz_set=1, intake_state="done")
    if profile:
        db.set_profile(uid, profile)
    return uid


async def say(text, ext="3003"):
    return await core.handle_text("telegram", ext, ext, text)


async def test_the_goal_question_offers_three_ideas_drawn_from_what_the_user_said(model):
    uid = make_user()
    reply = await say("/goal")
    assert "set a goal together" in reply
    assert all(f"{i}) {idea}" in reply for i, idea in enumerate(IDEAS, 1)) and "Reply 1, 2 or 3" in reply
    assert flows.get(uid)["options"] == IDEAS and model["suggested"] == 1


@pytest.mark.parametrize("answer", ["2", "2)", "option 2", "the second one", "second", "b"])
async def test_choosing_an_idea_by_number_saves_it_and_moves_on_to_the_first_step(model, answer):
    uid = make_user()
    await say("/goal")
    reply = await say(answer)
    assert f'Goal saved: "{IDEAS[1]}"' in reply and "small step toward" in reply
    assert [g["text"] for g in db.active_goals(uid)] == [IDEAS[1]]
    assert flows.get(uid)["flow"] == "objective"


async def test_greek_ways_of_choosing_work_too(model):
    uid = make_user(lang="el")
    await say("/goal")
    reply = await say("πρώτο")
    assert IDEAS[0] in reply and [g["text"] for g in db.active_goals(uid)] == [IDEAS[0]]


async def test_an_idea_can_be_chosen_and_adjusted_in_one_message(model):
    uid = make_user()
    await say("/goal")
    reply = await say("2, but only on weekdays")
    assert "Here's how I'd write that as a goal" in reply and IDEAS[1][:20] in reply  # the change was applied to idea 2
    assert model["drafts"][-1][1] == "but only on weekdays" and model["drafts"][-1][2] == IDEAS[1]
    assert db.active_goals(uid) == []  # not saved until they say yes
    assert "Goal saved" in await say("yes")


async def test_an_own_goal_still_goes_through_draft_and_confirm(model):
    uid = make_user()
    await say("/goal")
    assert "Here's how I'd write that as a goal" in await say("I want to stop doomscrolling at night")
    assert db.active_goals(uid) == []
    assert "Goal saved" in await say("yes")
    assert db.active_goals(uid)[0]["text"].startswith("goal: I want to stop doomscrolling")


async def test_a_number_outside_the_ideas_is_not_taken_as_a_choice(model):
    uid = make_user()
    await say("/goal")
    assert "a little more" in await say("4")
    assert db.active_goals(uid) == [] and flows.get(uid)["options"] == IDEAS


async def test_skip_still_works(model):
    make_user()
    await say("/goal")
    assert "Say /goal whenever" in await say("skip")


async def test_when_the_model_cannot_suggest_the_built_in_ideas_match_what_the_user_said(model, monkeypatch):
    async def broken(uid):
        raise RuntimeError("down")

    monkeypatch.setattr(llm, "suggest_goals", broken)
    uid = make_user(profile={"obstacle": "starting tasks and I keep getting distracted by my phone"})
    reply = await say("/goal")
    assert "Start my most important task within 10 minutes" in reply and "Work in one focused 25-minute block" in reply
    options = flows.get(uid)["options"]
    assert len(options) == len(set(options)) == 3


async def test_a_slow_model_does_not_hold_the_question_up(model, monkeypatch):
    async def slow(uid):
        await asyncio.sleep(5)
        return IDEAS

    monkeypatch.setattr(llm, "suggest_goals", slow)
    monkeypatch.setattr(flows, "SUGGEST_TIMEOUT", 0.05)
    make_user()
    reply = await say("/goal")
    assert "set a goal together" in reply and IDEAS[0] not in reply  # built-in ideas instead


async def test_fewer_than_three_ideas_from_the_model_means_the_built_in_ones(model, monkeypatch):
    async def two(uid):
        return IDEAS[:2]

    monkeypatch.setattr(llm, "suggest_goals", two)
    make_user()
    reply = await say("/goal")
    assert IDEAS[0] not in reply and "3) " in reply


def test_the_built_in_ideas_always_come_to_exactly_three_distinct_ones_in_the_users_language():
    uid = make_user()
    assert len(set(flows.fallback_goal_options(uid))) == 3  # nothing said yet: the general-purpose trio
    greek = make_user(lang="el", ext="3004", profile={"obstacle": "δεν μπορώ να κοιμηθώ και αγχώνομαι"})
    options = flows.fallback_goal_options(greek)
    assert len(set(options)) == 3 and any("κρεβάτι" in o for o in options) and any("διάλειμμα" in o for o in options)


async def test_after_the_interview_the_question_quotes_their_obstacle_and_offers_ideas(model):
    uid = make_user(profile={"obstacle": "starting tasks and keeping track of time"})
    reply = await flows.start_goal(uid, after_intake=True)
    assert 'You mentioned: "starting tasks and keeping track of time"' in reply and f"1) {IDEAS[0]}" in reply


async def test_suggest_goals_cleans_up_the_models_formatting(monkeypatch):
    uid = make_user(profile={"why": "my mornings fall apart", "obstacle": "starting tasks"})
    answers = iter(
        [
            '1. "Put my keys in one bowl by the door every evening for the next four weeks."\n- Plan tomorrow\'s first task each night, on 5 nights a week for a month.\n• Leave 10 minutes earlier on workdays for the next month.\n',
            "Just one line that is long enough to count",
            "",
        ]
    )
    seen = []

    async def scripted(messages, max_tokens=1500):
        seen.append(messages[0]["content"])
        return next(answers)

    monkeypatch.setattr(llm, "_complete", scripted)
    ideas = await llm.suggest_goals(uid)
    assert ideas == [
        "Put my keys in one bowl by the door every evening for the next four weeks.",
        "Plan tomorrow's first task each night, on 5 nights a week for a month.",
        "Leave 10 minutes earlier on workdays for the next month.",
    ]
    assert "my mornings fall apart" in seen[0] and "starting tasks" in seen[0]  # built from what the user said
    assert await llm.suggest_goals(uid) == []  # fewer than three usable lines
    assert await llm.suggest_goals(uid) == []  # empty answer


async def test_suggest_goals_has_nothing_to_go_on_for_a_brand_new_user(monkeypatch):
    uid = make_user()

    async def must_not_be_called(messages, max_tokens=1500):
        raise AssertionError("no material, so no model call")

    monkeypatch.setattr(llm, "_complete", must_not_be_called)
    assert await llm.suggest_goals(uid) == []


async def test_short_model_jobs_get_enough_tokens_for_a_reasoning_models_hidden_thinking(monkeypatch):
    uid = make_user(profile={"why": "my mornings fall apart", "obstacle": "starting tasks"})
    budgets = []

    async def capture(messages, max_tokens=1500):
        budgets.append(max_tokens)
        return "Put my keys in one bowl by the door every evening for the next four weeks."

    monkeypatch.setattr(llm, "_complete", capture)
    await llm.suggest_goals(uid)
    await llm.draft(uid, "goal", "I want to be on time")
    assert budgets and all(b >= 1500 for b in budgets)  # 400-600 was used up by the thinking and came back empty


async def test_the_goal_ideas_are_prepared_while_the_coach_writes_not_after_it(monkeypatch):
    import time

    uid = make_user(ext="3010")
    db.set_fields(uid, consent_at=1.0, tz_set=1, intake_state="")
    db.set_profile(uid, {"why": "x", "tried": "y", "obstacle": "z", "strength": "s", "rhythm": "r"})
    flows.put(uid, "intake", "mood", started=time.time())

    async def slow_reply(uid, text, instruction=None, coach=True):
        await asyncio.sleep(0.3)
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    async def slow_suggest(uid):
        await asyncio.sleep(0.3)
        return list(IDEAS)

    monkeypatch.setattr(llm, "reply", slow_reply)
    monkeypatch.setattr(llm, "suggest_goals", slow_suggest)
    monkeypatch.setattr(llm, "cards_read", lambda uid: set())
    started = time.monotonic()
    reply = await say("a bit stressed", "3010")
    elapsed = time.monotonic() - started
    assert reply.startswith("coached") and f"1) {IDEAS[0]}" in reply
    assert elapsed < 0.5  # about 0.3s (the two overlap), not 0.6s


async def test_ideas_prepared_for_a_question_that_gets_dropped_are_abandoned(monkeypatch):
    uid = make_user(ext="3011")
    for i in range(2):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    finished = []

    async def slow_suggest(uid):
        await asyncio.sleep(0.3)
        finished.append(1)
        return list(IDEAS)

    async def distressed_reply(uid, text, instruction=None, coach=True):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "reply", distressed_reply)
    monkeypatch.setattr(llm, "suggest_goals", slow_suggest)
    monkeypatch.setattr(llm, "cards_read", lambda uid: {"self_talk"})  # the coach ended up helping with distress
    reply = await say("I always mess everything up", "3011")
    await asyncio.sleep(0.4)
    assert reply == "coached" and flows.get(uid) is None and finished == []


async def test_ideas_are_started_during_the_interview_and_ready_when_the_goal_question_comes(monkeypatch):
    import time

    uid = make_user(ext="3020")
    db.set_fields(uid, intake_state="")
    started = []

    async def slow_suggest(uid):
        started.append(time.monotonic())
        await asyncio.sleep(0.15)
        return list(IDEAS)

    async def reply(uid, text, instruction=None, coach=True):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "suggest_goals", slow_suggest)
    monkeypatch.setattr(llm, "reply", reply)
    monkeypatch.setattr(llm, "cards_read", lambda uid: set())
    flows.put(uid, "intake", "why", started=time.time())
    await say("because mornings fall apart", "3020")
    await say("alarms", "3020")
    assert not started  # nothing yet: the obstacle has not been told
    await say("starting tasks", "3020")  # now the ideas begin to be worked out...
    await asyncio.sleep(0.01)
    assert len(started) == 1
    await asyncio.sleep(0.2)  # ...while the user answers the remaining questions
    for answer in ("I'm creative", "wake at 7"):
        await say(answer, "3020")
    began = time.monotonic()
    reply_text = await say("fine", "3020")  # last answer: the goal question needs no further waiting
    assert f"1) {IDEAS[0]}" in reply_text and len(started) == 1 and time.monotonic() - began < 0.1


async def test_if_the_early_ideas_are_still_not_ready_at_the_end_the_built_in_ones_are_used(monkeypatch):
    uid = make_user(ext="3021", profile={"obstacle": "starting tasks"})

    async def never(uid):
        await asyncio.sleep(30)
        return list(IDEAS)

    monkeypatch.setattr(llm, "suggest_goals", never)
    monkeypatch.setattr(flows, "SUGGEST_PREFETCH_WAIT", 0.05)
    flows.prefetch_goal_options(uid)
    options = await flows.goal_options(uid)
    assert options == flows.fallback_goal_options(uid) and uid not in flows._prefetched
