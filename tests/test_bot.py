from unittest.mock import AsyncMock, MagicMock

from telegram.ext import CommandHandler, MessageHandler

from minionbot.bot import BuildApplication, MakeCommandCallback, MakeFallbackCallback
from minionbot.config import Config
from minionbot.commands import DENIED_TEXT

from tests.conftest import OWNER_ID, STRANGER_ID


def FakeUpdate(userId, username=None):
    update = MagicMock()
    update.effective_user.id = userId
    update.effective_user.username = username
    update.effective_message.reply_text = AsyncMock()
    return update


def FakeContext(args):
    context = MagicMock()
    context.args = args
    return context


def test_build_application_registers_handlers(store, registry):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry)
    handlers = app.handlers[0]
    commandNames = {n for h in handlers if isinstance(h, CommandHandler) for n in h.commands}
    assert commandNames == set(registry.Names())
    assert isinstance(handlers[-1], MessageHandler)  # fallback last


async def test_callback_replies_to_known_user(store, registry):
    update = FakeUpdate(OWNER_ID)
    await MakeCommandCallback(registry, store, "users")(update, FakeContext(["list"]))
    assert "owner" in update.effective_message.reply_text.call_args[0][0]


async def test_callback_denies_stranger(store, registry):
    update = FakeUpdate(STRANGER_ID, "mallory")
    await MakeCommandCallback(registry, store, "users")(update, FakeContext(["list"]))
    update.effective_message.reply_text.assert_awaited_once_with(DENIED_TEXT)


async def test_fallback_denies_stranger(store, registry):
    update = FakeUpdate(STRANGER_ID)
    await MakeFallbackCallback(registry, store)(update, FakeContext(None))
    update.effective_message.reply_text.assert_awaited_once_with(DENIED_TEXT)


async def test_callback_ignores_updates_without_user(store, registry):
    update = MagicMock()
    update.effective_user = None
    await MakeCommandCallback(registry, store, "help")(update, FakeContext(None))
