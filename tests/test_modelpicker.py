import json
from types import SimpleNamespace as NS

import pytest

from duecoach import llm
from duecoach import modelpicker as mp


def entry(model, tools=True, context=262144, out=("text",)):
    return {
        "id": model,
        "context_length": context,
        "supported_parameters": ["tools", "reasoning"] if tools else ["reasoning"],
        "architecture": {"output_modalities": list(out)},
    }


def test_only_free_tool_capable_text_models_with_enough_context_are_candidates():
    assert mp.eligible(entry("nvidia/nemotron-3-ultra-550b-a55b:free"))
    assert not mp.eligible(entry("kilo-auto/free"))  # auto-routers send each request to some other model
    assert not mp.eligible(entry("openrouter/free"))
    assert not mp.eligible(entry("paid/model"))
    assert not mp.eligible(entry("a/b:free", tools=False))
    assert not mp.eligible(entry("a/b:free", context=8000))
    assert not mp.eligible(entry("cohere/north-mini-code:free"))  # denylist
    assert not mp.eligible(entry("nvidia/nemotron-3.5-content-safety:free"))
    assert not mp.eligible(entry("a/b:free", out=("text", "audio")))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Ωραία ρουτίνα! Θες να βάλουμε υπενθύμιση για τα φάρμακα στις 9:00;", True),
        ("Ωραία ρουτίνα με καφέ και Telegram.", True),  # a brand name in Latin letters is fine
        ("Πώς πάει η νychtolήμας σου;", False),  # Latin and Greek letters mixed inside one word
        ("Βάλε τα φάρμαка για αύριο.", False),  # Cyrillic letters inside a Greek word
        ("Ένα inverselyπוהײ ξεκίνημα.", False),  # Hebrew letters
        ("Good morning, let's plan your day.", False),  # not Greek
        ("", False),
    ],
)
def test_clean_greek(text, expected):
    assert mp.clean_greek(text) is expected


def tool_message(**args):
    return NS(tool_calls=[NS(function=NS(name="set_reminder", arguments=json.dumps(args)))])


def test_a_good_reminder_is_daily_and_at_nine():
    assert mp.good_reminder(tool_message(at="2026-10-09 09:00", daily=True, message="x"))
    assert not mp.good_reminder(tool_message(at="2026-10-09 09:00", message="x"))  # not daily
    assert not mp.good_reminder(tool_message(minutes=60, daily=True, message="x"))
    assert not mp.good_reminder(NS(tool_calls=None))


def test_rank_orders_by_speed_and_keeps_the_current_first_choice_unless_clearly_beaten():
    results = [mp.Probe("slow", True, 9.0), mp.Probe("fast", True, 4.0), mp.Probe("broken", False, 1.0), mp.Probe("mid", True, 6.0)]
    assert mp.rank(results) == ["fast", "mid", "slow"]
    close = [mp.Probe("current", True, 5.0), mp.Probe("new", True, 4.5)]
    assert mp.rank(close, "current") == ["current", "new"]  # only 10% faster: not worth switching
    clear = [mp.Probe("current", True, 8.0), mp.Probe("new", True, 4.0)]
    assert mp.rank(clear, "current") == ["new", "current"]


class FakeCompletions:
    """Answers per model: a clean Greek reply and a correct tool call, unless the model is scripted to misbehave."""

    def __init__(self, broken):
        self.broken = broken

    async def create(self, model, messages, tools=None, **kwargs):
        how = self.broken.get(model)
        if how == "error":
            raise RuntimeError("503 overloaded")
        if tools:
            args = {"at": "2026-10-09 09:00", "daily": True, "message": "φάρμακα"} if how != "no-tool" else None
            msg = NS(content="", tool_calls=[NS(function=NS(name="set_reminder", arguments=json.dumps(args)))] if args else None)
        else:
            text = "Καλημέρα με νychtolήμας" if how == "garbled" else "Ωραία αρχή! Τι θες να κάνουμε πρώτο;"
            msg = NS(content=text, tool_calls=None)
        return NS(choices=[NS(message=msg, finish_reason="length" if how == "cut" else "stop")])


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    state = {"broken": {}}
    monkeypatch.setattr(llm._client, "chat", NS(completions=FakeCompletions(state["broken"])))
    monkeypatch.setattr(mp, "STATE_FILE", str(tmp_path / "models.json"))
    monkeypatch.setattr(llm, "MODELS", ["seed"])
    monkeypatch.setattr(llm, "SEED_MODELS", ["seed"])
    monkeypatch.setattr(llm, "auto_info", {})
    return state


async def test_refresh_keeps_only_models_that_pass_and_puts_the_seed_last(gateway, monkeypatch):
    async def candidates():
        return ["good-a:free", "garbled:free", "cut:free", "no-tool:free", "down:free", "good-b:free"]

    monkeypatch.setattr(mp, "fetch_candidates", candidates)
    gateway["broken"].update({"garbled:free": "garbled", "cut:free": "cut", "no-tool:free": "no-tool", "down:free": "error"})
    picked = await mp.refresh()
    assert sorted(picked) == ["good-a:free", "good-b:free"]
    assert llm.MODELS == [*picked, "seed"]
    assert llm.auto_info["failed"]["garbled:free"] == "unclean Greek"
    assert llm.auto_info["failed"]["cut:free"] == "empty or cut off"
    assert llm.auto_info["failed"]["no-tool:free"] == "wrong tool call"
    assert "RuntimeError" in llm.auto_info["failed"]["down:free"]
    assert mp.load()["models"] == picked  # remembered across restarts


async def test_when_nothing_passes_the_current_list_is_left_alone(gateway, monkeypatch):
    async def candidates():
        return ["a:free"]

    monkeypatch.setattr(mp, "fetch_candidates", candidates)
    gateway["broken"]["a:free"] = "error"
    assert await mp.refresh() == []
    assert llm.MODELS == ["seed"] and mp.load() == {}


async def test_the_pool_is_capped(gateway, monkeypatch):
    async def candidates():
        return [f"m{i}:free" for i in range(8)]

    monkeypatch.setattr(mp, "fetch_candidates", candidates)
    monkeypatch.setattr(mp, "LLM_POOL_SIZE", 3)
    assert len(await mp.refresh()) == 3
