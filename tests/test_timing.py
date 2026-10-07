import time

import pytest

from duecoach import chat, core, db, flows, llm


@pytest.fixture
def model(monkeypatch):
    """Coaching replies are fixed; drafts echo the text."""

    async def fake_reply(uid, text, instruction=None):
        if text:  # like the real model call: both sides of the exchange are stored
            db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    async def fake_draft(uid, kind, text, previous=None, goal=""):
        return f"{kind}: {text}"

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setattr(llm, "draft", fake_draft)


def make_user(messages, ext="5005"):
    row = db.get_or_create_user("telegram", ext, ext, "en")
    db.set_fields(row["user_id"], consent_at=1.0, tz_set=1, intake_state="done")
    for i in range(messages):
        db.add_message(row["user_id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
    return row["user_id"]


async def say(text, ext="5005"):
    return await core.handle_text("telegram", ext, ext, text)


ASKS_FOR_GOAL = "set a goal together"


@pytest.mark.parametrize(
    ("text", "signal"),
    [
        ("I always forget my keys", "problem"),
        ("I keep losing track of time", "problem"),
        ("I can't focus in the morning", "problem"),
        ("I want to stop being late", "problem"),
        ("I'm always late for work", "problem"),
        ("I struggle with starting tasks", "problem"),
        ("Ξεχνάω συνέχεια τα κλειδιά μου", "problem"),
        ("Θέλω να οργανωθώ καλύτερα", "problem"),
        ("Δεν μπορώ να συγκεντρωθώ", "problem"),
        ("ok I'll try the bowl by the door", "step"),
        ("sounds good, I will put it near the door", "step"),
        ("Θα το δοκιμάσω αύριο", "step"),
        ("Εντάξει, θα βάλω ένα μπολ", "step"),
        ("hello", None),
        ("thanks!", None),
        ("what is body doubling", None),
        ("ευχαριστώ", None),
        ("it's a nice day", None),
    ],
)
def test_what_counts_as_naming_a_problem_or_agreeing_to_a_step(text, signal):
    assert flows.signal_in(text) == signal


@pytest.mark.parametrize(
    "text",
    [
        "I want to die",
        "I'm thinking of ending my life",
        "i want to kill myself",
        "δεν αντέχω άλλο",
        "θέλω να πεθάνω",
        "I was abused as a kid",
    ],
)
def test_distress_is_recognised_in_both_languages(text):
    assert flows.is_crisis(text)


async def test_the_goal_question_comes_when_a_problem_is_named_not_before(model):
    make_user(0)
    assert ASKS_FOR_GOAL not in await say("hello")  # two messages stored, nothing named yet
    reply = await say("I always forget my keys in the morning")
    assert reply.startswith("coached") and ASKS_FOR_GOAL in reply


async def test_without_a_signal_the_count_fallback_asks_after_about_three_exchanges(model):
    uid = make_user(0)
    for chatter in ("hello", "just saying hi", "how are things"):
        assert ASKS_FOR_GOAL not in await say(chatter) or db.message_count(uid) >= flows.GOAL_FALLBACK_MESSAGES
    assert flows.get(uid) is not None  # asked once the fallback count was reached (the third reply)


async def test_nothing_is_asked_in_the_very_first_exchange(model):
    uid = make_user(0)
    assert await say("I want to stop being late") == "coached"  # the coach answers first, with nothing added
    assert db.message_count(uid) >= flows.GOAL_MIN_MESSAGES
    assert ASKS_FOR_GOAL in await say("I always forget my keys")  # a full exchange has happened: now it fits


async def test_never_during_a_crisis_even_with_a_problem_named(model):
    uid = make_user(8)
    reply = await say("I always feel like this and I want to die")
    assert "112" in reply and flows.get(uid) is None  # the safety message, not a coaching reply with a question attached


async def test_never_when_the_coach_is_helping_with_distress(model):
    make_user(8)
    assert await say("I always get so anxious before work") == "coached"
    assert ASKS_FOR_GOAL in await say("I always run late too")  # a calmer moment: now it fits


@pytest.mark.parametrize("question_mark", ["?", ";", ";"])
async def test_not_when_the_user_asked_a_question_themselves(model, question_mark):
    make_user(8)
    assert await say(f"I always forget things, what can I do{question_mark}") == "coached"


async def test_questions_are_never_back_to_back(model):
    uid = make_user(2)
    assert ASKS_FOR_GOAL in await say("I always forget my keys")
    await say("skip")
    assert await say("I keep losing my wallet too") == "coached"  # too soon after our last question
    for i in range(flows.QUESTION_GAP):
        db.add_message(uid, "user" if i % 2 == 0 else "assistant", f"later {i}")
    assert db.get_user(uid)["goal_asked_at"] > 0  # (the goal question itself waits a week after a skip)


async def test_agreeing_to_a_step_offers_to_save_it_as_this_weeks_step(model):
    uid = make_user(8)
    db.add_goal(uid, "Arrive on time")
    reply = await say("ok I'll put a bowl by the door and drop my keys in it")
    assert "I'd write the step like this" in reply and "objective: ok I'll put a bowl" in reply
    assert flows.get(uid)["step"] == "confirm"
    assert "small reward" in await say("yes")
    assert "(reward: coffee)" in await say("coffee")
    (step,) = db.open_objectives(uid)
    assert step["goal_id"] == db.active_goals(uid)[0]["id"] and step["incentive"] == "coffee"


async def test_no_step_offer_while_a_step_is_already_open_or_just_declined(model):
    uid = make_user(8)
    db.add_goal(uid, "Arrive on time")
    db.add_objective(uid, None, "existing step", "")
    assert await say("ok I'll try the bowl idea") == "coached"
    db._conn.execute("DELETE FROM objectives")
    db.set_field(uid, "obj_asked_at", time.time() - 3600)  # they said skip an hour ago
    assert await say("fine, I will put it by the door too") == "coached"


async def test_a_followup_waits_for_a_calmer_moment(model):
    uid = make_user(8)
    db.add_goal(uid, "g")
    db.add_objective(uid, None, "tidy the desk", "")
    db._conn.execute("UPDATE objectives SET created=?", (time.time() - 3 * 86400,))
    assert "112" in await say("I want to die") and flows.get(uid) is None
    for i in range(flows.QUESTION_GAP):
        db.add_message(uid, "user", f"x{i}")
    assert "How did this go" in await say("hi again")


async def test_the_timezone_question_respects_the_same_rules(model):
    uid = make_user(8)
    db.set_field(uid, "tz_set", 0)
    db.add_goal(uid, "g")
    db.add_objective(uid, None, "s", "")
    assert "where do you live" not in await say("how do I begin?")
    assert "where do you live" in await say("let me think about it")


async def test_the_fixed_questions_from_before_still_work_end_to_end(model):
    uid = make_user(2)
    assert ASKS_FOR_GOAL in await say("I keep forgetting my keys")
    assert "Does that fit" in await say("keys by the door")
    assert "Goal saved" in await say("yes")
    assert db.active_goals(uid)


async def test_the_coach_is_told_not_to_ask_its_own_question_when_ours_follows(model, monkeypatch):
    seen = []

    async def recording_reply(uid, text, instruction=None):
        seen.append(instruction)
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "coached")
        return "coached"

    monkeypatch.setattr(llm, "reply", recording_reply)
    make_user(2)
    await say("hello there")  # nothing due: no instruction
    assert seen[-1] is None
    reply = await say("I always forget my keys")  # the goal question will follow
    assert ASKS_FOR_GOAL in reply and "Do NOT end your reply with a question" in seen[-1]
    await say("skip")
    await say("I keep losing my wallet")  # too soon after our question: nothing follows, so the coach may ask its own
    assert seen[-1] is None


async def test_a_question_that_was_due_is_not_asked_when_the_person_is_struggling(model):
    uid = make_user(2)
    reply = await say("I always mess everything up")
    assert reply == "coached" and flows.get(uid) is None and db.get_user(uid)["question_at_count"] == -100


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Put keys by the door. Do you have a bowl?", "Put keys by the door."),
        ("Good start. Try a bowl. What would work for you? And when?", "Good start. Try a bowl."),
        ("Βάλε τα κλειδιά στην πόρτα. Έχεις μπολ;", "Βάλε τα κλειδιά στην πόρτα."),
        ("A tip.\n\nWhat is your setup?", "A tip."),
        ("Do you have a bowl?", "Do you have a bowl?"),  # a lone question is kept: never send nothing
        ("Put keys by the door.", "Put keys by the door."),
    ],
)
def test_the_coachs_closing_question_is_dropped_when_ours_follows(reply, expected):
    assert chat.without_trailing_question(reply) == expected


async def test_the_reply_and_our_question_do_not_both_end_with_a_question(model, monkeypatch):
    async def asks_anyway(uid, text, instruction=None):
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "x")
        return "Keep them in a bowl by the door. Do you have one?"

    monkeypatch.setattr(llm, "reply", asks_anyway)
    make_user(2)
    reply = await say("I always forget my keys")
    assert reply.startswith("Keep them in a bowl by the door.\n\n") and "Do you have one" not in reply and ASKS_FOR_GOAL in reply


async def test_crisis_wording_never_becomes_the_answer_to_a_pending_question(model, monkeypatch):
    uid = make_user(2)
    assert ASKS_FOR_GOAL in await say("I always forget my keys")  # a goal question is now waiting for an answer
    assert flows.get(uid) is not None

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("the safety reply must not depend on the model")

    monkeypatch.setattr(llm, "reply", must_not_be_called)
    monkeypatch.setattr(llm, "draft", must_not_be_called)
    reply = await say("I want to die")
    assert "112" in reply and "Are you safe right now" in reply and "goal" not in reply.lower()
    assert flows.get(uid) is None and db.active_goals(uid) == []
    user = db.get_user(uid)
    assert user["snooze_until"] > time.time() + 20 * 3600  # proactive check-ins pause for a day
    assert db.recent_messages(uid, 2)[-1]["content"] == reply  # the coach will see it when the conversation continues


async def test_crisis_wording_also_cancels_the_timezone_question_and_works_in_greek(model):
    uid = make_user(8)
    db.set_fields(uid, tz_set=0, lang="el")
    flows.put(uid, "timezone", "asked", attempts=0)
    reply = await say("δεν αντέχω άλλο")
    assert "112" in reply and "ασφαλής" in reply
    assert db.get_user(uid)["tz_state"] == "" and flows.get(uid) is None


async def test_a_configured_local_helpline_is_added(model, monkeypatch):
    make_user(8)
    monkeypatch.setattr(chat, "CRISIS_HELP", "In Greece you can also call 1018.")
    assert "In Greece you can also call 1018." in await say("I want to kill myself")


async def test_someone_in_crisis_gets_the_safety_message_even_before_agreeing_and_nothing_is_stored(model):
    row = db.get_or_create_user("telegram", "6006", "6006", "en")  # new: has not agreed to the privacy notice yet
    reply = await say("I want to end my life", "6006")
    assert "112" in reply and "AI coaching assistant" not in reply
    assert db.message_count(row["user_id"]) == 0
