import asyncio
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .. import core, strings
from ..config import is_allowed

log = logging.getLogger("coach.telegram")


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str):
        # concurrent updates: one user's slow model call must not hold up everyone else (core serialises each user's own messages)
        self.app = Application.builder().token(token).concurrent_updates(True).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._on_text))
        self.app.add_handler(MessageHandler(filters.ATTACHMENT | filters.LOCATION | filters.CONTACT, self._on_other))
        self.app.add_error_handler(self._on_error)

    @staticmethod
    async def _keep_typing(chat) -> None:
        """Telegram's typing indicator lasts about 5 seconds, so renew it while the reply is being written."""
        while True:
            try:
                await chat.send_action("typing")
            except Exception:
                return
            await asyncio.sleep(4)

    async def _answer(self, update: Update, text: str | None) -> None:
        user, chat = update.effective_user, update.effective_chat
        if user is None or chat is None or update.message is None:
            return
        typing = asyncio.create_task(self._keep_typing(chat)) if is_allowed(self.name, str(user.id)) else None
        try:
            lang = strings.lang_from_telegram(user.language_code)
            if text is None:
                reply = await core.handle_unsupported(self.name, str(user.id), str(chat.id), lang)
            else:
                reply = await core.handle_text(self.name, str(user.id), str(chat.id), text, lang)
        finally:
            if typing:
                typing.cancel()
        if reply:
            await update.message.reply_text(reply)

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, update.message.text if update.message else None)

    async def _on_other(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, None)  # voice note, photo, file...: say we only read text

    @staticmethod
    async def _on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("update handling failed", exc_info=ctx.error)

    def can_send(self, user, template_ok: bool = True) -> bool:
        return True

    async def send(self, user, text: str) -> None:
        await self.app.bot.send_message(int(user["chat_id"]), text)

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
