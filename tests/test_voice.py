import json

import httpx
import pytest

from duecoach import chat, core, db, llm, ratelimit


@pytest.fixture
def voice_on(monkeypatch):
    monkeypatch.setattr(core, "VOICE_MODEL", "gemini-4-argon")
    monkeypatch.setattr(core, "VOICE_URL", "https://example.test/{model}:generateContent")


@pytest.fixture
def coach(monkeypatch):
    """The coach answers with a fixed line and remembers what it was told."""
    heard = []

    async def fake_reply(uid, text, instruction=None):
        heard.append(text)
        db.add_message(uid, "user", text)
        db.add_message(uid, "assistant", "Got it.")
        return "Got it."

    monkeypatch.setattr(llm, "reply", fake_reply)
    return heard


def agreed_user(ext="7007"):
    row = db.get_or_create_user("telegram", ext, ext, "en")
    db.set_fields(row["user_id"], consent_at=1.0)
    return row["user_id"]


def transcribing(monkeypatch, text="remind me to call mum at five"):
    async def fake(uid, audio, mime="audio/ogg"):
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(llm, "transcribe", fake)


class Audio:
    def __init__(self):
        self.fetched = 0

    async def __call__(self):
        self.fetched += 1
        return b"ogg-bytes"


async def voice(seconds=5, fetch=None, ext="7007"):
    fetch = fetch or Audio()
    result = await core.handle_voice("telegram", ext, ext, seconds, fetch)
    return result.text, fetch


async def test_a_voice_note_is_handled_like_the_same_words_typed_and_shows_what_was_heard(voice_on, coach, monkeypatch):
    agreed_user()
    transcribing(monkeypatch)
    reply, _ = await voice()
    assert coach == ["remind me to call mum at five"]
    assert reply == "🎤 “remind me to call mum at five”\n\nGot it."


async def test_nothing_is_downloaded_or_transcribed_before_the_user_agreed(voice_on, coach, monkeypatch):
    db.get_or_create_user("telegram", "7008", "7008", "en")  # new: has not agreed yet
    transcribing(monkeypatch, RuntimeError("must not be called"))
    reply, fetch = await voice(ext="7008")
    assert fetch.fetched == 0 and coach == [] and "AI coaching assistant" in reply


async def test_a_voice_note_that_is_a_crisis_gets_the_safety_message_alone(voice_on, coach, monkeypatch):
    agreed_user()
    transcribing(monkeypatch, "I want to die")
    reply, _ = await voice()
    assert "112" in reply and "🎤" not in reply


async def test_a_note_that_is_too_long_is_not_downloaded(voice_on, coach, monkeypatch):
    agreed_user()
    transcribing(monkeypatch, RuntimeError("must not be called"))
    reply, fetch = await voice(seconds=core.VOICE_MAX_SECONDS + 1)
    assert fetch.fetched == 0 and "too long" in reply


@pytest.mark.parametrize(("heard", "expected"), [(RuntimeError("provider down"), "couldn't listen"), ("", "couldn't make out")])
async def test_a_failed_or_empty_transcription_is_explained_and_nothing_reaches_the_coach(voice_on, coach, monkeypatch, heard, expected):
    agreed_user()
    transcribing(monkeypatch, heard)
    reply, _ = await voice()
    assert expected in reply and coach == []


async def test_voice_is_off_without_a_voice_model(coach, monkeypatch):
    agreed_user()
    monkeypatch.setattr(core, "VOICE_URL", "")
    reply, fetch = await voice()
    assert fetch.fetched == 0 and "text and voice" in reply


async def test_a_rate_limited_user_is_not_transcribed(voice_on, coach, monkeypatch):
    uid = agreed_user()
    transcribing(monkeypatch, RuntimeError("must not be called"))
    monkeypatch.setattr(ratelimit, "full", lambda u: u == uid)
    reply, fetch = await voice()
    assert fetch.fetched == 0 and reply == chat.say(uid, "RATE_LIMITED")


# --- the call to Gemini's own API ---


def gemini(monkeypatch, handler):
    monkeypatch.setattr(llm, "VOICE_URL", "https://example.test/{model}:generateContent")
    monkeypatch.setattr(llm, "VOICE_MODEL", "gemini-4-argon")
    monkeypatch.setattr(llm, "_voice_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_the_audio_is_sent_as_inline_data_with_the_users_session_and_the_text_comes_back(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "plan", "thought": True}, {"text": " Γεια σου "}]}}]}
        )

    gemini(monkeypatch, handler)
    monkeypatch.setattr(llm, "LLM_BASE_URL", "https://opencode.ai/zen/go/v1")
    assert await llm.transcribe(1001, b"abc") == "Γεια σου"
    inline = seen["body"]["contents"][0]["parts"][1]["inlineData"]
    assert inline == {"mimeType": "audio/ogg", "data": "YWJj"}
    assert seen["url"].endswith("/gemini-4-argon:generateContent")
    assert seen["headers"]["x-opencode-session"] == llm._session_id(1001) and seen["headers"]["authorization"].startswith("Bearer ")


@pytest.mark.parametrize("answer", ["[unclear]", "  ", "[Unclear]."])
async def test_no_words_means_an_empty_transcript(monkeypatch, answer):
    gemini(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": answer}]}}]}))
    assert await llm.transcribe(1001, b"abc") == ""


async def test_a_failing_provider_is_retried_once_then_raises(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": "down"})

    async def no_wait(seconds):
        return None

    gemini(monkeypatch, handler)
    monkeypatch.setattr(llm.asyncio, "sleep", no_wait)
    with pytest.raises(llm.ModelError):
        await llm.transcribe(1001, b"abc")
    assert len(calls) == llm.MODEL_ATTEMPTS


async def test_only_a_coach_reply_is_spoken_never_a_notice_or_the_safety_message(voice_on, coach, monkeypatch):
    agreed_user()
    transcribing(monkeypatch)
    result = await core.handle_voice("telegram", "7007", "7007", 5, Audio())
    assert result.spoken == "Got it." and result.lang == "en" and result.text.endswith("Got it.")
    transcribing(monkeypatch, "I want to die")
    crisis = await core.handle_voice("telegram", "7007", "7007", 5, Audio())
    assert "112" in crisis.text and crisis.spoken is None
    transcribing(monkeypatch, "")
    assert (await core.handle_voice("telegram", "7007", "7007", 5, Audio())).spoken is None
