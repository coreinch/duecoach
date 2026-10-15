import json
from types import SimpleNamespace as NS

import pytest

from duecoach import db, llm, prompts, tools

REAL_COMPLETE = llm._complete  # conftest blocks the real one for every test; the recorder tests put it back


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(llm, "_complete", REAL_COMPLETE)


def message(content=None, calls=()):
    tool_calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)] or None
    return NS(content=content, tool_calls=tool_calls)


@pytest.fixture
def model(monkeypatch):
    """Script the model: each queued item is the message returned by one call; every call is recorded."""
    queue, calls = [], []

    async def fake_chat(messages, tool_defs=None, max_tokens=1500):
        calls.append({"messages": list(messages), "tools": tool_defs})
        return queue.pop(0)

    monkeypatch.setattr(llm, "_chat", fake_chat)
    return NS(queue=queue, calls=calls)


async def test_one_call_answers_and_the_whole_playbook_is_already_in_the_prompt(model, user):
    model.queue += [message("Try a 5-minute timer.")]
    assert await llm.reply(1001, "I can't start") == "Try a 5-minute timer."
    assert len(model.calls) == 1
    system = model.calls[0]["messages"][0]["content"]
    assert "Shrink the task" in system and all(f"{c['id']} (use when" in system for c in llm.playbook.CARDS)
    assert [m["role"] for m in db.recent_messages(1001, 5)] == ["user", "assistant"]


async def test_tools_change_state_and_are_logged_without_their_arguments(model, user, caplog):
    secret = "my secret goal about my divorce"
    model.queue += [
        message(calls=[("add_goal", {"text": secret})]),
        message("Saved."),
    ]
    with caplog.at_level("INFO", logger="duecoach.llm"):
        await llm.reply(1001, "hi")
    assert db.active_goals(1001)[0]["text"] == secret
    assert secret not in caplog.text and "tool add_goal -> ok" in caplog.text


async def test_empty_answers_are_retried_then_fall_back_in_the_users_language(model, user):
    model.queue += [message(None), message("  "), message("")]
    db.set_field(1001, "lang", "el")
    out = await llm.reply(1001, "hi")
    assert out == llm.strings.t("el", "EMPTY_REPLY") and "Δεν κατάφερα" in out
    assert len(model.calls) == 3  # the answer, retried twice
    assert [m["role"] for m in db.recent_messages(1001, 5)] == ["user"]  # the apology is not stored as the coach's own words


async def test_a_reply_cut_off_by_the_token_limit_is_retried_then_trimmed(model, user):
    cut = message("Try a timer. Then Ο")
    cut.finish_reason = "length"
    model.queue += [cut, message("Try a 5-minute timer.")]
    assert await llm.reply(1001, "I can't start") == "Try a 5-minute timer."
    cut2 = [message("Try a timer for five minutes. Then Ο") for _ in range(3)]
    for m in cut2:
        m.finish_reason = "length"
    model.queue += cut2
    assert await llm.reply(1001, "again") == "Try a timer for five minutes."


async def test_check_ins_send_the_instruction_but_store_only_the_reply(model, user):
    model.queue += [message("How is the first step going?")]
    await llm.reply(1001, "", "PULSE INSTRUCTION")
    last = model.calls[0]["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith("[Automatic check-in trigger. This is not a message from the user.")
    assert last["content"].endswith("PULSE INSTRUCTION")
    assert db.recent_messages(1001, 5) == [{"role": "assistant", "content": "How is the first step going?"}]


def test_history_keeps_at_most_two_assistant_messages_in_a_row(user):
    db.add_message(1001, "user", "hello")
    for i in range(6):
        db.add_message(1001, "assistant", f"pulse {i}")
    db.add_message(1001, "user", "I'm back")
    history = llm._history(1001)
    assert [m["content"] for m in history] == ["hello", "pulse 4", "pulse 5", "I'm back"]


def test_the_system_prompt_carries_the_date_time_notes_and_playbook(user):
    db.set_field(1001, "notes", "likes timers")
    system = llm._system(1001)
    assert "Now: " in system and "Europe/Athens" in system and "likes timers" in system and "start_sprint (use when" in system


class FakeCompletions:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    async def create(self, **kwargs):
        self.calls += 1
        return self.answers.pop(0)


def outage():
    return NS(choices=None, error={"message": "provider_unavailable", "code": 502})


async def test_a_provider_outage_is_retried_once_then_reported_clearly(monkeypatch):
    ok = NS(choices=[NS(message=message("fine"))])
    fake = FakeCompletions([outage(), ok])
    monkeypatch.setattr(llm._client, "chat", NS(completions=fake))
    monkeypatch.setattr(llm.asyncio, "sleep", lambda s: _noop())
    assert (await llm._chat([{"role": "user", "content": "hi"}])).content == "fine" and fake.calls == 2

    fake = FakeCompletions([outage(), outage()])
    monkeypatch.setattr(llm._client, "chat", NS(completions=fake))
    with pytest.raises(llm.ModelError, match="provider_unavailable"):
        await llm._chat([{"role": "user", "content": "hi"}])
    assert fake.calls == 2


async def _noop():
    return None


async def test_the_tool_guard_comes_last_in_a_chat_turn_and_just_before_a_check_ins_instruction(model, user):
    model.queue += [message("Hi."), message("How is it going?")]
    await llm.reply(1001, "are you working now?")
    assert model.calls[0]["messages"][-1] == {"role": "system", "content": llm.prompts.TOOL_GUARD}
    await llm.reply(1001, "", "PULSE INSTRUCTION")
    guard, trigger = model.calls[1]["messages"][-2:]
    assert guard["content"] == llm.prompts.PROACTIVE_GUARD and trigger["content"].endswith("PULSE INSTRUCTION")


async def _as_awaitable(value):
    return value


async def test_leaked_reasoning_is_stripped_from_a_reply(monkeypatch):
    msg = NS(content="<think>\nthe user greets me\n</think>\n\nΓεια σου!", finish_reason=None, tool_calls=None)
    fake = NS(create=lambda **kw: _as_awaitable(NS(choices=[NS(message=msg, finish_reason="stop")])))
    monkeypatch.setattr(llm._client, "chat", NS(completions=fake))
    assert (await llm._chat([{"role": "user", "content": "hi"}])).content == "Γεια σου!"


async def test_the_configured_model_is_the_only_one_used_and_minimax_is_asked_to_split_its_reasoning(monkeypatch):
    seen = []

    async def create(**kw):
        seen.append(kw)
        return NS(choices=[NS(message=NS(content="ok", tool_calls=None), finish_reason="stop")])

    monkeypatch.setattr(llm._client, "chat", NS(completions=NS(create=create)))
    monkeypatch.setattr(llm, "_EXTRA", {"extra_body": {"reasoning_split": True}})
    await llm._chat([{"role": "user", "content": "hi"}])
    assert seen[0]["model"] == llm.LLM_MODEL and seen[0]["extra_body"] == {"reasoning_split": True}
    assert llm.status() == {"model": llm.LLM_MODEL}


async def test_a_check_in_gets_no_tools_but_a_reply_to_the_user_does(model, user):
    model.queue += [message("Hi."), message("How is it going?")]
    await llm.reply(1001, "hello")
    await llm.reply(1001, "", "PULSE INSTRUCTION")
    assert model.calls[0]["tools"] == llm.tools.TOOLS and model.calls[1]["tools"] is None


def test_the_check_in_trigger_says_how_long_ago_the_user_last_wrote():
    assert llm._since(0) == "" and llm._since(None) == ""
    assert "30 minutes ago" in llm._since(llm.time.time() - 1800)
    assert "5 hours ago" in llm._since(llm.time.time() - 5 * 3600)
    assert "3 days ago" in llm._since(llm.time.time() - 3 * 86400)
    assert "NOT over until the Now line" in llm._since(llm.time.time() - 1800)  # a 12:45 appointment is not over at 10:00


@pytest.mark.parametrize("goodbye", ["Talk tomorrow.", "Sleep well.", "Have a good evening.", "Καληνύχτα, τα λέμε αύριο.", "ok"])
async def test_a_check_in_that_is_a_goodbye_or_a_few_words_is_redrawn_and_never_sent(model, user, goodbye):
    model.queue += [message(goodbye), message("Good morning. What is the one thing you want to move today?")]
    out = await llm.reply(1001, "", llm.prompts.MORNING)
    assert out == "Good morning. What is the one thing you want to move today?" and len(model.calls) == 2
    model.queue += [message(goodbye)] * 3
    with pytest.raises(llm.ModelError):
        await llm.reply(1001, "", llm.prompts.PULSE)
    assert [m["content"] for m in db.recent_messages(1001, 9) if m["role"] == "assistant"] == [out]  # the goodbye is not stored


async def test_the_evening_wrap_up_may_say_goodnight_but_not_be_empty(model, user):
    model.queue += [message("That was a solid day, rest now and sleep well.")]
    assert "sleep well" in await llm.reply(1001, "", llm.prompts.EVENING)
    model.queue += [message("")] * 3
    with pytest.raises(llm.ModelError):
        await llm.reply(1001, "", llm.prompts.EVENING)  # an empty check-in is an error, not the apology sent to the user


async def test_the_check_in_trigger_says_a_goodbye_in_the_chat_is_over(model, user):
    model.queue += [message("How is the first step going today?")]
    await llm.reply(1001, "", "PULSE INSTRUCTION")
    assert "never answer with a sign-off" in model.calls[0]["messages"][-1]["content"]


def test_markdown_is_stripped_but_plain_text_is_left_alone():
    raw = "**Phase 1** foundations\n## Next\n* buy the domain\n- keep this 2*3 and __that__"
    assert llm._plain(raw) == "Phase 1 foundations\nNext\n- buy the domain\n- keep this 2*3 and that"


LEAK = '[Calling set_reminder with parameter at = "2026-10-11 07:00", message = "Ξυπνητήρι 7:00", daily = false]'


@pytest.mark.parametrize(
    "text",
    [
        LEAK,
        "Sure. set_reminder(minutes=5)",
        '{"name": "add_goal", "arguments": {}}',
        "<tool_call>x</tool_call>",
        "I will call save_to_toolbox now.",
    ],
)
def test_tool_calls_written_as_text_are_recognised(text):
    assert llm._leaked_call(text)


@pytest.mark.parametrize("text", ["I'll remind you at 7:00.", "Θα σου θυμίσω στις 9:00 για τα φάρμακα.", "Calling it a day is fine too."])
def test_ordinary_replies_are_not_mistaken_for_tool_calls(text):
    assert not llm._leaked_call(text)


async def test_a_check_in_that_writes_a_tool_call_is_redrawn_and_never_sent(model, user):
    model.queue += [message(LEAK), message("Good morning. What is the first thing you want to do today?")]
    assert await llm.reply(1001, "", llm.prompts.MORNING) == "Good morning. What is the first thing you want to do today?"
    model.queue += [message(LEAK)] * 3
    with pytest.raises(llm.ModelError):
        await llm.reply(1001, "", llm.prompts.PULSE)
    assert not any("Calling" in m["content"] for m in db.recent_messages(1001, 9))


async def test_a_chat_reply_that_writes_a_tool_call_gets_the_apology_not_the_call(model, user):
    model.queue += [message(LEAK)] * 3
    out = await llm.reply(1001, "remind me at 7")
    assert out == llm.strings.t("en", "EMPTY_REPLY") and "Calling" not in out


def test_stored_leaks_and_old_flow_markers_are_not_shown_to_the_model_again(user):
    db.add_message(1001, "user", "(step result) missed, barrier: something else")
    db.add_message(1001, "assistant", LEAK)
    db.add_message(1001, "user", "(finished the intake interview)")
    db.add_message(1001, "user", "hello")
    assert llm._history(1001) == [{"role": "user", "content": "hello"}]


def _local_clock(due):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.fromtimestamp(due, ZoneInfo(db.get_user(1001)["tz"])).strftime("%H:%M")


async def test_a_wrong_clock_time_after_setting_a_reminder_is_corrected_to_the_tool_time(model, user):
    model.queue += [
        message(None, calls=[("set_reminder", {"minutes": 60, "message": "Stand up"})]),
        message("OK, I'll remind you in an hour, at 21:03, to stand up."),
    ]
    out = await llm.reply(1001, "remind me in an hour")
    real = _local_clock(db.pending_reminders(1001)[0]["due"])
    assert out == f"OK, I'll remind you in an hour, at {real}, to stand up." and "21:03" not in out or real == "21:03"


async def test_times_the_tool_reported_or_the_user_gave_are_left_alone(model, user):
    model.queue += [
        message(None, calls=[("set_reminder", {"minutes": 60, "message": "Call"})]),
        message("Set. Your call at 15:00 is first, the reminder follows."),
    ]
    out = await llm.reply(1001, "remind me in an hour, my call is at 15:00")
    assert "15:00" in out and len(model.calls) == 2


async def test_two_unexplained_times_are_redrawn_with_the_real_time_in_the_hint(model, user):
    model.queue += [
        message(None, calls=[("set_reminder", {"minutes": 30, "message": "Walk"})]),
        message("Set for 03:11 and again 04:22."),
        message("Done, I will remind you soon."),
    ]
    assert await llm.reply(1001, "remind me in half an hour") == "Done, I will remind you soon."
    hint = model.calls[2]["messages"][-1]
    assert hint["role"] == "system" and "03:11" in hint["content"] and "04:22" in hint["content"]


def test_no_times_are_checked_when_no_reminder_was_set():
    assert llm._fix_reminder_times("Try 6:30 or 7:00.", [], "help me wake up") == ("Try 6:30 or 7:00.", [])
    assert llm._fix_reminder_times("At 9:00.", ["error: nope"], "x") == ("At 9:00.", [])


EARLIER = "I'll check in after the timer to see how many you got through."


async def test_a_sentence_the_coach_already_said_is_redrawn_without_it(model, user):
    db.add_message(1001, "assistant", f"Go for it. {EARLIER}")
    model.queue += [
        message("Nice, keep going. I'll check in after 3 PM to see how many you got through."),
        message("Nice, keep going. Tell me when the timer is done."),
    ]
    assert await llm.reply(1001, "started") == "Nice, keep going. Tell me when the timer is done."
    hint = model.calls[1]["messages"][-1]
    assert hint["role"] == "system" and "repeats what you already said" in hint["content"] and "check in after 3 PM" in hint["content"]


async def test_if_it_keeps_repeating_the_repeated_sentence_is_cut_not_the_whole_reply(model, user):
    db.add_message(1001, "assistant", EARLIER)
    model.queue += [message(f"Good pace. {EARLIER}")] * 3
    assert await llm.reply(1001, "still going") == "Good pace."
    db.add_message(1001, "assistant", EARLIER)
    model.queue += [message(EARLIER)] * 3
    assert await llm.reply(1001, "ok") == EARLIER  # nothing else to say: the reply is kept rather than emptied


def test_short_sentences_and_different_sentences_are_not_repeats(user):
    db.add_message(1001, "assistant", "Go. Nice work. Timer set for 14:39, you will hear from me then.")
    assert llm._repeated("Go. Nice work. Open the folder and type the first title.", 1001) == []


async def test_record_ids_are_not_spoken_and_are_cut_if_the_model_insists(model, user):
    model.queue += [message("Saved as goal #1, invoices cleared by Thursday.")] * 3
    out = await llm.reply(1001, "ok")
    assert "#" not in out and out.startswith("Saved as goal")


# --- the recorder: what the user agreed to is saved by the code, not left to the coach's tool call ---


async def test_an_agreed_goal_is_saved_before_the_coach_answers_and_the_coach_is_told(model, user, recorder):
    db.add_message(1001, "assistant", 'A goal that could work: "Send every invoice within 48 hours for the next month". Does that fit?')
    model.queue += [
        message('{"goal": "Send every invoice within 48 hours for the next month.", "step": null}'),
        message("Got it. What is the first invoice you will send?"),
    ]
    assert await llm.reply(1001, "yes") == "Got it. What is the first invoice you will send?"
    assert [g["text"] for g in db.active_goals(1001)] == ["Send every invoice within 48 hours for the next month."]
    told = [m["content"] for m in model.calls[1]["messages"] if m["role"] == "system"]
    assert any("Already saved for them just now: goal saved" in t for t in told)


async def test_an_agreed_step_is_saved_with_the_goal_and_a_claim_of_saving_is_then_allowed(model, user, recorder):
    goal_id = int(run_tool("add_goal", text="Send invoices on time").split("#")[1].split()[0])
    db.add_message(1001, "assistant", "First step: open the invoice folder today. Want a timer?")
    model.queue += [
        message('{"goal": null, "step": "Open the invoice folder today."}'),
        message("Saved. Starting the timer when you say go."),
    ]
    assert (await llm.reply(1001, "yes I'll do that today")).startswith("Saved.")
    (step,) = db.open_objectives(1001)
    assert step["text"] == "Open the invoice folder today." and step["goal_id"] == goal_id


async def test_nothing_is_saved_when_the_user_only_answers_a_question_or_the_check_cannot_be_read(model, user, recorder):
    db.add_message(1001, "assistant", "How many invoices are there?")
    model.queue += [message('{"goal": null, "step": null}'), message("Okay, six. Which is the oldest?")]
    await llm.reply(1001, "six")
    db.add_message(1001, "assistant", "Want to set a goal around that?")
    model.queue += [message("this is not json"), message("Tell me more about it.")]
    await llm.reply(1001, "maybe")
    assert db.active_goals(1001) == [] and db.open_objectives(1001) == []


async def test_the_check_is_skipped_when_there_is_a_goal_and_a_step_or_no_earlier_coach_message(model, user, recorder):
    model.queue += [message("Hi.")]
    await llm.reply(1001, "hello")  # no earlier coach message: one call only
    assert len(model.calls) == 1
    db.add_goal(1001, "g")
    db.add_objective(1001, None, "s", "")
    db.add_message(1001, "assistant", "Anything else?")
    model.queue += [message("Sure.")]
    await llm.reply(1001, "yes")
    assert len(model.calls) == 2


def run_tool(name, **args):
    return llm.tools.run_tool(1001, name, json.dumps(args))


# --- timers, reasoning, human claims and ADHD lectures ---


async def test_while_a_timer_runs_short_updates_get_one_specific_sentence_not_a_word_or_the_timer(model, user):
    db.add_reminder(1001, llm.time.time() + 600, "Stand up")
    model.queue += [
        message("Go."),
        message("Keep the timer running while you type the client name."),
        message("Type the client name first, then the amount."),
    ]
    assert await llm.reply(1001, "ok") == "Type the client name first, then the amount."


@pytest.mark.parametrize(
    "text",
    [
        "I can see the coaching record is empty. I should acknowledge what they shared.",
        "The user said they struggle with invoices.",
        "Tired here too, the light is doing that Sunday thing.",
        "When I owe something, my brain tries not to look at it.",
    ],
)
async def test_replies_that_narrate_the_coachs_reasoning_or_claim_a_human_life_never_reach_the_user(model, user, text):
    model.queue += [message(text)] * 3
    assert await llm.reply(1001, "hi") == llm.strings.t("en", "EMPTY_REPLY")


async def test_explaining_adhd_to_someone_who_never_mentioned_it_is_redrawn_or_cut(model, user):
    model.queue += [
        message("Starting and not finishing is common with ADHD. Which invoice is the oldest?"),
        message("Which invoice is the oldest?"),
    ]
    assert await llm.reply(1001, "I never finish things") == "Which invoice is the oldest?"
    model.queue += [message("It happens a lot with ADHD. What is the newest invoice about?")] * 3
    assert await llm.reply(1001, "still stuck") == "What is the newest invoice about?"
    db.add_message(1001, "user", "my ADHD makes this hard")
    model.queue += [message("With ADHD, tiny first steps work best. What is the first one?")]
    assert "ADHD" in await llm.reply(1001, "yes")


async def test_the_verifier_sends_a_false_cancel_back_and_the_coach_then_cancels(model, user, recorder, verifier):
    reminder = tools.run_tool(1001, "set_reminder", json.dumps({"minutes": 10, "message": "Pick up the parcel."}))
    assert reminder.startswith("ok") and len(db.pending_reminders(1001)) == 1
    rid = db.pending_reminders(1001)[0]["id"]
    model.queue += [
        message("Nice, I am cancelling that reminder then."),  # the draft claims it, no tool ran
        message('{"unbacked": ["says the reminder is cancelled"], "unhandled": ["cancel_reminder for the parcel reminder"]}'),
        message(calls=[("cancel_reminder", {"reminder_id": rid})]),
        message("Nice, the reminder is gone."),
        message('{"unbacked": [], "unhandled": []}'),
    ]
    assert await llm.reply(1001, "i did it") == "Nice, the reminder is gone."
    assert db.pending_reminders(1001) == []


async def test_a_verifier_that_fails_never_blocks_the_reply(model, user, recorder, verifier):
    model.queue += [message("Sounds good."), message("this is not json")]
    assert await llm.reply(1001, "ok") == "Sounds good."


async def test_the_verifier_may_send_a_draft_back_only_twice(model, user, recorder, verifier):
    flagged = message('{"unbacked": ["claims a save"], "unhandled": []}')
    model.queue += [message("Fine."), flagged, message("Fine, then."), flagged, message("Fine, really."), flagged]
    assert await llm.reply(1001, "ok") == "Fine, really."


async def test_the_verifier_refuses_saved_without_a_save_tool_and_the_coach_then_saves(model, user, recorder, verifier):
    model.queue += [
        message("Saved. Starting the timer when you say go."),
        message('{"unbacked": ["says the goal is saved"], "unhandled": ["add_goal for the tax return"]}'),
        message(calls=[("add_goal", {"text": "File my tax return."})]),
        message("Goal saved."),
        message('{"unbacked": [], "unhandled": []}'),
    ]
    assert await llm.reply(1001, "yes, that is my goal") == "Goal saved."
    assert [g["text"] for g in db.active_goals(1001)] == ["File my tax return."]


async def test_the_verifier_sends_back_time_is_up_while_the_timer_still_runs(model, user, recorder, verifier):
    tools.run_tool(1001, "set_reminder", json.dumps({"minutes": 20, "message": "Sprint is over."}))
    model.queue += [
        message("Time's up! How did it go?"),
        message('{"unbacked": ["says time is up but the timer has 20 minutes left"], "unhandled": []}'),
        message("Still about 20 minutes on the clock. What are you working on?"),
        message('{"unbacked": [], "unhandled": []}'),
    ]
    assert (await llm.reply(1001, "done early?")).startswith("Still about 20 minutes")


async def test_the_verifier_sends_back_a_reply_that_ignores_what_the_user_asked(model, user, recorder, verifier):
    model.queue += [
        message("You found the fix, and now it is done. What is next?"),
        message('{"unbacked": [], "unhandled": [], "off_target": ["user asked what the message meant; the draft assumes they finished"]}'),
        message("That was the reminder for the scraper sprint. Do you still want to do it?"),
        message('{"unbacked": [], "unhandled": [], "off_target": []}'),
    ]
    assert (await llm.reply(1001, "what?")).startswith("That was the reminder")
    assert "check found" in model.calls[2]["messages"][-1]["content"]


async def test_a_clean_verifier_pass_is_logged(model, user, recorder, verifier, caplog):
    model.queue += [message("Sounds good."), message('{"unbacked": [], "unhandled": [], "off_target": []}')]
    with caplog.at_level("INFO", logger="duecoach.llm"):
        await llm.reply(1001, "ok")
    assert "verifier ok" in caplog.text


async def test_the_verifier_sends_back_a_check_in_that_invents_what_the_user_did(model, user, recorder, verifier):
    db.add_message(1001, "user", "I will have my coffee first")
    model.queue += [
        message("Glad you rested after your coffee. Will you make the call now or later?"),
        message('{"unbacked": [], "unhandled": [], "off_target": ["says they rested, which they never said"]}'),
        message("How is the coffee break going? Will you make the call now or later?"),
        message('{"unbacked": [], "unhandled": [], "off_target": []}'),
    ]
    assert await llm.reply(1001, "", prompts.PULSE) == "How is the coffee break going? Will you make the call now or later?"
    assert "Rewrite the check-in" in model.calls[2]["messages"][-1]["content"]
    assert "I will have my coffee first" in model.calls[1]["messages"][0]["content"]  # the last chat line is shown to the verifier
