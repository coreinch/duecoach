import asyncio
import logging

from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import HISTORY_TURNS, LLM_API_KEY, LLM_BASE_URL, LLM_CONCURRENCY, LLM_MODEL, LLM_TIMEOUT

log = logging.getLogger("coach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent

READ_PLAYBOOK = {"type": "function", "function": {"name": "get_strategy"}}

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
_slots = asyncio.Semaphore(LLM_CONCURRENCY)  # one slow or rate-limited gateway must not be hammered by every user at once


def _system(user_id: int) -> str:
    notes = db.get_notes(user_id)
    lang = db.get_user(user_id)["lang"]
    return (
        prompts.SYSTEM
        + "\n"
        + prompts.LANGUAGE.get(lang, prompts.LANGUAGE["en"])
        + (f"\n\nWhat you know about the user:\n{notes}" if notes else "")
        + "\n\nPlaybook index (technique ids you can read with get_strategy):\n"
        + playbook.index_text()
        + "\n\n"
        + tools.coaching_state(user_id)
    )


def _history(user_id: int) -> list[dict]:
    """Recent chat for the model, keeping at most a couple of assistant messages in a row (the latest ones)."""
    out: list[dict] = []
    for msg in db.recent_messages(user_id, HISTORY_TURNS):
        out.append(msg)
        run = 0
        for m in reversed(out):
            if m["role"] != "assistant":
                break
            run += 1
        if run > MAX_CONSECUTIVE_ASSISTANT:
            del out[-run]  # drop the oldest message of the run
    return out


async def _chat(messages: list[dict], tool_defs: list[dict] | None = None, max_tokens: int = 1500, force: dict | None = None):
    kwargs = {"tools": tool_defs} if tool_defs else {}
    if force:
        kwargs["tool_choice"] = force
    async with _slots:
        resp = await _client.chat.completions.create(model=LLM_MODEL, messages=messages, max_tokens=max_tokens, **kwargs)
    return resp.choices[0].message


async def _complete(messages: list[dict], max_tokens: int = 1500) -> str:
    return ((await _chat(messages, max_tokens=max_tokens)).content or "").strip()


async def reply(user_id: int, user_text: str, instruction: str | None = None, coach: bool = True) -> str:
    """Chat with history. `instruction` is a hidden steering note for this turn (e.g. a /stuck command).

    With coach=True (the default) the model must read a playbook card before it answers, on every turn.
    """
    if user_text:
        db.add_message(user_id, "user", user_text)
    messages = [{"role": "system", "content": _system(user_id)}, *_history(user_id)]
    if instruction:
        messages.append({"role": "system", "content": instruction})
    text, empty_retries, force = "", 0, READ_PLAYBOOK if coach else None
    for _ in range(MAX_TOOL_ROUNDS + MAX_EMPTY_RETRIES):
        msg = await _chat(messages, tools.TOOLS, force=force)
        force = None  # only the first round is forced; after that the model answers or uses other tools
        text = (msg.content or "").strip()
        if not msg.tool_calls:
            if text or empty_retries >= MAX_EMPTY_RETRIES:
                break
            empty_retries += 1  # the free models sometimes return only hidden reasoning: ask again
            log.warning("empty model reply, retrying (%d)", empty_retries)
            continue
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in msg.tool_calls
                ],
            }
        )
        for call in msg.tool_calls:
            result = tools.run_tool(user_id, call.function.name, call.function.arguments)
            # log the outcome, not the arguments: they contain what the user wrote about their life
            log.info("tool %s -> %s", call.function.name, result.split(":", 1)[0])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    text = text or strings.t(db.get_user(user_id)["lang"], "EMPTY_REPLY")
    db.add_message(user_id, "assistant", text)
    return text


async def refresh_notes(user_id: int) -> None:
    """Fold recent conversation into long-term notes every 20 messages."""
    if db.message_count(user_id) % 20 != 0:
        return
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in db.recent_messages(user_id, 40))
    prompt = prompts.SUMMARIZE.format(
        notes=db.get_notes(user_id) or "(none)", convo=convo, language=prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English")
    )
    notes = await _complete([{"role": "user", "content": prompt}], max_tokens=1200)
    if notes:
        db.set_field(user_id, "notes", notes)
