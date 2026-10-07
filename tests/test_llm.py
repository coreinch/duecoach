import json
from types import SimpleNamespace as NS

import pytest

from coach import db, llm


def message(content=None, calls=()):
    tool_calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)] or None
    return NS(content=content, tool_calls=tool_calls)


@pytest.fixture
def model(monkeypatch):
    """Script the model: each queued item is the message returned by one call; every call is recorded."""
    queue, calls = [], []

    async def fake_chat(messages, tool_defs=None, max_tokens=1500, force=None):
        calls.append({"messages": list(messages), "force": force})
        return queue.pop(0)

    monkeypatch.setattr(llm, "_chat", fake_chat)
    return NS(queue=queue, calls=calls)


async def test_the_first_round_is_forced_to_read_the_playbook_and_the_card_reaches_the_model(model, user):
    model.queue += [message(calls=[("get_strategy", {"id": "start_sprint"})]), message("Try a 5-minute timer.")]
    assert await llm.reply(1001, "I can't start") == "Try a 5-minute timer."
    assert model.calls[0]["force"] == llm.READ_PLAYBOOK and model.calls[1]["force"] is None
    tool_result = model.calls[1]["messages"][-1]
    assert tool_result["role"] == "tool" and "Shrink the task" in tool_result["content"]
    assert [m["role"] for m in db.recent_messages(1001, 5)] == ["user", "assistant"]


async def test_notes_style_calls_skip_the_forced_lookup(model, user):
    model.queue += [message("Here is your note.")]
    await llm.reply(1001, "/progress", "write a note", coach=False)
    assert model.calls[0]["force"] is None


async def test_tools_change_state_and_are_logged_without_their_arguments(model, user, caplog):
    secret = "my secret goal about my divorce"
    model.queue += [
        message(calls=[("get_strategy", {"id": "goal_wording"})]),
        message(calls=[("add_goal", {"text": secret})]),
        message("Saved."),
    ]
    with caplog.at_level("INFO", logger="coach.llm"):
        await llm.reply(1001, "hi")
    assert db.active_goals(1001)[0]["text"] == secret
    assert secret not in caplog.text and "tool add_goal -> ok" in caplog.text


async def test_empty_answers_are_retried_then_fall_back_in_the_users_language(model, user):
    model.queue += [message(calls=[("get_strategy", {"id": "overload"})]), message(None), message("  "), message("")]
    db.set_field(1001, "lang", "el")
    out = await llm.reply(1001, "hi")
    assert out == "Είμαι εδώ. Θέλεις να μου πεις τι συμβαίνει;"
    assert len(model.calls) == 4  # forced round + the answer retried twice


async def test_check_ins_send_the_instruction_but_store_only_the_reply(model, user):
    model.queue += [message(calls=[("get_strategy", {"id": "start_sprint"})]), message("How is the first step going?")]
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
    assert "Now: " in system and "Europe/Athens" in system and "likes timers" in system and "- start_sprint:" in system


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
