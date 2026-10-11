import json
from types import SimpleNamespace as NS

import pytest

from duecoach import db, llm


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
