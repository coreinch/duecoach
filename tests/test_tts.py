import base64
import json

import httpx
import pytest

from duecoach import tts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**Great** job 🎉 — see https://example.com now", "Great job — see now"),
        ("• first thing\n• second thing", "first thing second thing"),
        ("I saved task #12 for you.", "I saved task for you."),
        ("Γεια σου! Τι κάνεις;", "Γεια σου! Τι κάνεις;"),
    ],
)
def test_the_reply_is_cleaned_into_plain_speech(text, expected):
    assert tts.speakable(text) == expected


def test_a_long_reply_is_spoken_up_to_the_last_full_sentence_that_fits():
    text = "First sentence here. Second sentence follows. Third sentence is long enough to cross the limit for sure."
    assert tts.speakable(text, limit=50) == "First sentence here. Second sentence follows."


def test_one_very_long_sentence_stops_at_a_word():
    assert tts.speakable("word " * 50, limit=22) == "word word word word"


def with_key(monkeypatch, handler):
    monkeypatch.setattr(tts, "GOOGLE_TTS_API_KEY", "test-key")
    monkeypatch.setattr(tts, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_the_greek_standard_voice_is_asked_for_ogg_opus_with_the_key_in_a_header(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(body=json.loads(request.content), key=request.headers["x-goog-api-key"], url=str(request.url))
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"opus-bytes").decode()})

    with_key(monkeypatch, handler)
    assert await tts.synthesize("Γεια σου", "el") == b"opus-bytes"
    assert seen["body"]["voice"] == {"languageCode": "el-GR", "name": "el-GR-Standard-A"}
    assert seen["body"]["audioConfig"] == {"audioEncoding": "OGG_OPUS"} and seen["body"]["input"] == {"text": "Γεια σου"}
    assert seen["key"] == "test-key" and "key=" not in seen["url"]  # never in the URL, which gets logged


async def test_english_uses_the_english_voice(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(body=json.loads(request.content))
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"x").decode()})

    with_key(monkeypatch, handler)
    await tts.synthesize("Hello there", "en")
    assert seen["body"]["voice"]["languageCode"] == "en-US"


async def test_no_audio_without_a_key_or_text_or_when_the_call_fails(monkeypatch):
    def boom(request):
        return httpx.Response(500, json={"error": "down"})

    with_key(monkeypatch, boom)
    assert await tts.synthesize("Hello there", "en") is None  # the call failed: the text reply stands
    assert await tts.synthesize("🎉 https://example.com", "en") is None  # nothing to say
    monkeypatch.setattr(tts, "GOOGLE_TTS_API_KEY", "")
    assert await tts.synthesize("Hello there", "en") is None and not tts.enabled()
