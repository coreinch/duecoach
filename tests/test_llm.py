import json
from types import SimpleNamespace as NS

import httpx
import openai
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


class PerModel:
    """Scripted gateway: each model name has its own queue of answers (a reply, an outage body, or an exception to raise)."""

    def __init__(self, **queues):
        self.queues = {name.replace("_", "-"): list(items) for name, items in queues.items()}
        self.calls = []

    async def create(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        item = self.queues[model].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def good(text="fine"):
    return NS(choices=[NS(message=message(text))])


def connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "http://gateway"))


def bad_request():
    request = httpx.Request("POST", "http://gateway")
    return openai.BadRequestError("tools not supported", response=httpx.Response(400, request=request), body=None)


@pytest.fixture
def chain(monkeypatch):
    """Three models in preference order, no cooldown history, and a clock the test controls."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(llm, "MODELS", ["primary", "backup-1", "backup-2"])
    monkeypatch.setattr(llm, "_down_until", {})
    monkeypatch.setattr(llm.time, "time", lambda: clock["t"])
    monkeypatch.setattr(llm, "LLM_MODEL_COOLDOWN", 120)

    def install(gateway):
        monkeypatch.setattr(llm._client, "chat", NS(completions=gateway))
        return gateway

    install.clock = clock
    return install


async def ask():
    return (await llm._chat([{"role": "user", "content": "hi"}])).content


async def test_the_first_model_is_used_when_it_works(chain):
    gateway = chain(PerModel(primary=[good("one")]))
    assert await ask() == "one" and gateway.calls == ["primary"]


async def test_a_failing_model_falls_through_to_the_next_and_is_skipped_for_a_while(chain):
    gateway = chain(PerModel(primary=[outage(), good("back")], backup_1=[good("b1"), good("b1 again")], backup_2=[]))
    assert await ask() == "b1" and gateway.calls == ["primary", "backup-1"]
    assert await ask() == "b1 again" and gateway.calls[2:] == ["backup-1"]  # primary is cooling down: no wait on it
    chain.clock["t"] += 121
    assert await ask() == "back" and gateway.calls[3:] == ["primary"]  # cooldown over: the preferred model is tried first again


async def test_connection_errors_and_rejected_requests_also_fall_through(chain):
    gateway = chain(PerModel(primary=[connection_error()], backup_1=[bad_request()], backup_2=[good("third")]))
    assert await ask() == "third" and gateway.calls == ["primary", "backup-1", "backup-2"]
    assert set(llm._down_until) == {"primary"}  # a request one model rejects doesn't make it "down" for everyone else


async def test_when_every_model_fails_the_error_names_each_one_and_all_are_tried_again_next_time(chain):
    gateway = chain(PerModel(primary=[outage(), good("ok")], backup_1=[connection_error()], backup_2=[outage()]))
    with pytest.raises(llm.ModelError) as error:
        await ask()
    assert all(name in str(error.value) for name in ("primary", "backup-1", "backup-2"))
    assert await ask() == "ok"  # all were cooling down, so the preferred one was tried first rather than giving up
    assert gateway.calls == ["primary", "backup-1", "backup-2", "primary"]


async def test_a_model_that_recovers_leaves_the_cooldown_list(chain):
    chain(PerModel(primary=[outage(), good("recovered")], backup_1=[good("b1")], backup_2=[]))
    await ask()
    assert "primary" in llm._down_until
    chain.clock["t"] += 121
    await ask()
    assert "primary" not in llm._down_until


async def test_the_total_time_budget_stops_further_models_after_slow_failures(chain, monkeypatch):
    class Slow(PerModel):
        async def create(self, **kwargs):
            chain.clock["t"] += 100  # every attempt takes 100 seconds before failing
            return await super().create(**kwargs)

    gateway = chain(Slow(primary=[outage()], backup_1=[outage()], backup_2=[good("never reached")]))
    monkeypatch.setattr(llm, "LLM_BUDGET", 150)
    with pytest.raises(llm.ModelError, match="budget"):
        await ask()
    assert gateway.calls == ["primary", "backup-1"]  # the third model was not tried


async def test_a_tool_calls_extra_content_is_sent_back_with_it(model, user):
    """Gemini 3 rejects the follow-up request if the thought signature it attached to a tool call is not returned."""
    call = NS(
        id="c0",
        function=NS(name="save_to_toolbox", arguments=json.dumps({"text": "timer"})),
        model_extra={"extra_content": {"google": {"thought_signature": "abc"}}},
    )
    model.queue += [NS(content=None, tool_calls=[call]), message("Saved.")]
    await llm.reply(1001, "hi")
    sent = model.calls[1]["messages"]
    assert [m for m in sent if m["role"] == "assistant"][0]["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "abc"}}


async def test_the_tool_guard_comes_last_in_a_chat_turn_and_just_before_a_check_ins_instruction(model, user):
    model.queue += [message("Hi."), message("How is it going?")]
    await llm.reply(1001, "are you working now?")
    assert model.calls[0]["messages"][-1] == {"role": "system", "content": llm.prompts.TOOL_GUARD}
    await llm.reply(1001, "", "PULSE INSTRUCTION")
    assert [m["content"] for m in model.calls[1]["messages"][-2:]] == [llm.prompts.TOOL_GUARD, "PULSE INSTRUCTION"]


def test_cloudflare_models_use_their_own_client_and_are_ignored_without_credentials(monkeypatch):
    cf = object()
    monkeypatch.setattr(llm, "_cf_client", cf)
    assert llm._client_for("@cf/meta/llama-4-scout-17b-16e-instruct") is cf
    assert llm._client_for("gemini-3.5-flash-lite") is llm._client
    assert llm._usable("@cf/meta/llama-4-scout-17b-16e-instruct") and llm._usable("gemini-3.5-flash-lite")
    monkeypatch.setattr(llm, "_cf_client", None)
    assert llm._client_for("@cf/meta/llama-4-scout-17b-16e-instruct") is llm._client
    assert not llm._usable("@cf/meta/llama-4-scout-17b-16e-instruct") and llm._usable("gemini-3.5-flash-lite")


def test_minimax_models_use_their_own_client_ask_for_split_reasoning_and_need_a_key(monkeypatch):
    mm = object()
    monkeypatch.setattr(llm, "_mm_client", mm)
    assert llm._client_for("MiniMax-M3") is mm and llm._usable("MiniMax-M3")
    assert llm._extra("MiniMax-M3") == {"extra_body": {"reasoning_split": True}} and llm._extra("gemini-3.5-flash-lite") == {}
    monkeypatch.setattr(llm, "_mm_client", None)
    assert not llm._usable("MiniMax-M3") and llm._client_for("MiniMax-M3") is llm._client


async def test_leaked_reasoning_is_stripped_from_a_reply(monkeypatch):
    msg = NS(content="<think>\nthe user greets me\n</think>\n\nΓεια σου!", finish_reason=None, tool_calls=None)
    fake = NS(create=lambda **kw: _as_awaitable(NS(choices=[NS(message=msg, finish_reason="stop")])))
    monkeypatch.setattr(llm._client, "chat", NS(completions=fake))
    monkeypatch.setattr(llm, "MODELS", ["primary"])
    assert (await llm._chat([{"role": "user", "content": "hi"}])).content == "Γεια σου!"


async def _as_awaitable(value):
    return value
