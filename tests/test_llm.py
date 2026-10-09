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
        calls.append({"messages": list(messages)})
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
    assert model.calls[0]["messages"][-1] == {"role": "system", "content": "PULSE INSTRUCTION"}
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
    assert [m["content"] for m in model.calls[1]["messages"][-2:]] == [llm.prompts.TOOL_GUARD, "PULSE INSTRUCTION"]


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
