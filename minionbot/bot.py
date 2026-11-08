"""Thin python-telegram-bot adapter around the CommandRegistry."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from minionbot.commands import CommandRegistry
from minionbot.config import Config
from minionbot.users import UserStore

PRIVATE = filters.ChatType.PRIVATE


def MakeCommandCallback(registry: CommandRegistry, store: UserStore, name: str):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, message = update.effective_user, update.effective_message
        if user is None or message is None:
            return
        reply = registry.Dispatch(name, list(context.args or []), user.id, user.username, store)
        await message.reply_text(reply)

    return Callback


def MakeFallbackCallback(registry: CommandRegistry, store: UserStore):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, message = update.effective_user, update.effective_message
        if user is None or message is None:
            return
        await message.reply_text(registry.DispatchFallback(user.id, user.username, store))

    return Callback


def BuildApplication(config: Config, store: UserStore, registry: CommandRegistry) -> Application:
    application = ApplicationBuilder().token(config.botToken).build()
    for name in registry.Names():
        application.add_handler(CommandHandler(name, MakeCommandCallback(registry, store, name), filters=PRIVATE))
    # Everything else (plain text, unknown commands): deny strangers, hint to others.
    application.add_handler(MessageHandler(PRIVATE, MakeFallbackCallback(registry, store)))
    return application


def ConfigureLogging() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs full request URLs, which contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
