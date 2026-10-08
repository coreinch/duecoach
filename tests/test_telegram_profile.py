from unittest.mock import AsyncMock

from duecoach import telegram_profile
from duecoach.commands import COMMANDS


def test_menu_only_lists_real_commands_in_both_languages():
    assert set(telegram_profile.MENU["en"]) == set(telegram_profile.MENU["el"])
    assert set(telegram_profile.MENU["en"]) <= set(COMMANDS)


def test_texts_fit_telegram_limits():
    for code in ("en", "el"):
        assert len(telegram_profile.SHORT[code]) <= 120
        assert len(telegram_profile.ABOUT[code]) <= 512
        assert all(1 <= len(text) <= 256 for text in telegram_profile.MENU[code].values())


async def test_apply_sets_default_and_each_language():
    bot = AsyncMock()
    await telegram_profile.apply(bot)
    bot.set_my_name.assert_awaited_once_with("duecoach")
    languages = [call.kwargs["language_code"] for call in bot.set_my_commands.await_args_list]
    assert languages == [None, "en", "el"]
