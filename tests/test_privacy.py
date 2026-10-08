import pytest

from duecoach import chat, db


def _uid(lang: str) -> int:
    ext_id = "1" if lang == "en" else "2"
    return db.get_or_create_user("telegram", ext_id, ext_id, lang)["user_id"]


def test_notice_names_the_bot_and_the_configured_retention(monkeypatch):
    monkeypatch.setattr(chat, "RETENTION_DAYS", 30)
    monkeypatch.setattr(chat, "INACTIVE_DELETE_DAYS", 200)
    for lang in ("en", "el"):
        text = chat.privacy(_uid(lang))
        assert "Duecoach" in text and "30" in text and "200" in text and "{" not in text


@pytest.mark.parametrize("lang", ["en", "el"])
def test_notice_leaves_out_periods_that_are_switched_off(monkeypatch, lang):
    monkeypatch.setattr(chat, "RETENTION_DAYS", 0)
    monkeypatch.setattr(chat, "INACTIVE_DELETE_DAYS", 0)
    text = chat.privacy(_uid(lang))
    assert "{" not in text and not any(char.isdigit() for char in text.replace("112", ""))
