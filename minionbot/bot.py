"""Thin python-telegram-bot adapter around the CommandRegistry and the social downloader."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message, MessageEntity, Update
from telegram.constants import ChatAction, ChatType
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from minionbot.commands import UNKNOWN_COMMAND_TEXT, CommandRegistry
from minionbot.config import Config
from minionbot.notify import Notifier
from minionbot.social.format import BuildTexts
from minionbot.social.models import PHOTO, DownloadError, LocalMedia, Post, UnexpectedDownloadError
from minionbot.social.service import SocialService
from minionbot.social.triggers import IGNORE, NO_LINKS, Decide, Incoming
from minionbot.users import UserStore

LOGGER = logging.getLogger(__name__)

PRIVATE = filters.ChatType.PRIVATE
PRIVACY_ON_KEY = "groupPrivacyOn"
ALBUM_SIZE = 10
NO_LINKS_TEXT = "I couldn't find a supported link (Instagram, Facebook, Twitter/X, Threads)."
PRIVACY_HINT_TEXT = (
    "I couldn't find a supported link. If you replied to a message, Telegram didn't pass it to me because my "
    "group privacy mode is on. My owner can fix it: @BotFather /setprivacy -> Disable, then re-add me to the group. "
    "Meanwhile you can send the link together with my @name."
)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
UPLOAD_TIMEOUT = 180


def MakeCommandCallback(registry: CommandRegistry, store: UserStore, name: str, notifier: Notifier | None = None):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, message = update.effective_user, update.effective_message
        if user is None or message is None:
            return
        errors: list[Exception] = []
        reply = registry.Dispatch(name, list(context.args or []), user.id, user.username, store, onError=errors.append)
        await message.reply_text(reply)
        for error in errors:
            if notifier:
                await notifier.ReportFailure(f"command /{name}", error)

    return Callback


def ToIncoming(message: Message | None) -> Incoming | None:
    if message is None:
        return None
    entities = message.entities or message.caption_entities or ()
    urls = [e.url for e in entities if e.type == MessageEntity.TEXT_LINK and e.url]
    return Incoming(message.text or message.caption or "", urls)


def VideoShape(item: LocalMedia) -> dict:
    """width/height/duration make Telegram show the video in its real aspect ratio."""
    shape = {"width": item.width, "height": item.height, "duration": item.duration}
    return {key: value for key, value in shape.items() if value}


def ReplySource(message: Message) -> Incoming | None:
    """What the user replied to: the replied-to message, plus a quoted excerpt or external reply if present."""
    replied = ToIncoming(message.reply_to_message)
    texts, urls = ([replied.text], list(replied.urls)) if replied else ([], [])
    quote = getattr(message, "quote", None)
    if quote is not None and getattr(quote, "text", None):
        texts.append(quote.text)
    external = getattr(message, "external_reply", None)  # reply to a message from another chat/topic
    preview = getattr(external, "link_preview_options", None)
    if preview is not None and getattr(preview, "url", None):
        urls.append(preview.url)
    if not texts and not urls:
        return None
    return Incoming("\n".join(texts), urls)


def DescribeMessage(message: Message | None) -> str:
    """Shape of a message for diagnostics. Never includes its content."""
    if message is None:
        return "none"
    entities = list(message.entities or ()) + list(message.caption_entities or ())
    kinds = sorted({str(e.type) for e in entities})
    text = message.text or message.caption or ""
    return f"{len(text)} chars, entities={kinds}, forwarded={getattr(message, 'forward_origin', None) is not None}"


async def SendMedia(message: Message, post: Post, caption: str) -> None:
    for start in range(0, len(post.media), ALBUM_SIZE):
        group = post.media[start : start + ALBUM_SIZE]
        groupCaption = caption if start == 0 and caption else None
        timeouts = {"write_timeout": UPLOAD_TIMEOUT, "read_timeout": UPLOAD_TIMEOUT}
        if len(group) == 1:
            item = group[0]
            if item.kind == PHOTO:
                await message.reply_photo(item.path.read_bytes(), caption=groupCaption, **timeouts)
            else:
                await message.reply_video(
                    item.path.read_bytes(), caption=groupCaption, supports_streaming=True, **VideoShape(item), **timeouts
                )
            continue
        album = []
        for index, item in enumerate(group):
            itemCaption = groupCaption if index == 0 else None
            if item.kind == PHOTO:
                album.append(InputMediaPhoto(item.path.read_bytes(), caption=itemCaption))
            else:
                album.append(InputMediaVideo(item.path.read_bytes(), caption=itemCaption, supports_streaming=True, **VideoShape(item)))
        await message.reply_media_group(album, **timeouts)


async def ReplyWithPost(message: Message, post: Post) -> None:
    caption, texts = BuildTexts(post)
    if post.media:
        await SendMedia(message, post, caption)
    for text in texts:
        await message.reply_text(text, link_preview_options=NO_PREVIEW)


def MakeMessageCallback(store: UserStore, service: SocialService, notifier: Notifier | None = None):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        incoming = ToIncoming(message)
        replied = ReplySource(message)
        decision = Decide(chat.type == ChatType.PRIVATE, context.bot.username or "", incoming, replied)
        LOGGER.info("Message in %s chat %s from user %s: %s, %d link(s)", chat.type, chat.id, sender.id, decision.kind, len(decision.links))
        if decision.kind == IGNORE:
            return
        if store.Authenticate(sender.id, sender.username) is None:
            await message.reply_text(store.DeniedText())
            return
        if decision.kind == NO_LINKS:
            LOGGER.warning(
                "No links found. message: %s | reply_to_message: %s | quote: %s | external_reply: %s",
                DescribeMessage(message),
                DescribeMessage(message.reply_to_message),
                getattr(message, "quote", None) is not None,
                getattr(message, "external_reply", None) is not None,
            )
            lostReply = (
                decision.mentioned
                and chat.type != ChatType.PRIVATE
                and message.reply_to_message is None
                and context.application.bot_data.get(PRIVACY_ON_KEY) is True
            )
            await message.reply_text(PRIVACY_HINT_TEXT if lostReply else NO_LINKS_TEXT if decision.mentioned else UNKNOWN_COMMAND_TEXT)
            return
        for link in decision.links:
            await chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            with tempfile.TemporaryDirectory(prefix="minionbot-") as directory:
                try:
                    post = await service.Load(link, Path(directory))
                    await ReplyWithPost(message, post)
                except DownloadError as error:
                    await message.reply_text(f"{error}\n{link.url}", link_preview_options=NO_PREVIEW)
                    if isinstance(error, UnexpectedDownloadError) and notifier:
                        await notifier.ReportFailure(f"downloading a {link.network} link", error.__cause__ or error)
                except Exception as error:
                    LOGGER.exception("Failed to reply for %s", link.url)
                    await message.reply_text(f"Sorry, I couldn't send the result for {link.url}", link_preview_options=NO_PREVIEW)
                    if notifier:
                        await notifier.ReportFailure(f"sending the result of a {link.network} link", error)

    return Callback


async def LogBotInfo(application: Application) -> None:
    """Log who we are and whether Telegram will deliver ordinary group messages to us."""
    me = await application.bot.get_me()
    if me.can_read_all_group_messages:
        privacy = "OFF (bot receives all group messages)"
    else:
        privacy = "ON (in groups the bot receives only commands and replies to its own messages; @mentions are not guaranteed)"
    application.bot_data[PRIVACY_ON_KEY] = not me.can_read_all_group_messages
    LOGGER.info("Running as @%s; group privacy mode: %s", me.username, privacy)


def MakePostInit(notifier: Notifier, restartInfo: str = ""):
    async def PostInit(application: Application) -> None:
        await LogBotInfo(application)
        me = await application.bot.get_me()
        await notifier.NotifyStartup(me.username, application.bot_data.get(PRIVACY_ON_KEY), restartInfo)

    return PostInit


def MakeErrorHandler(notifier: Notifier):
    """Handler for exceptions raised outside our own try/except blocks (and polling errors)."""

    async def OnError(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        if error is None:
            return
        where = "polling / updates"
        if isinstance(update, Update) and update.effective_chat is not None:
            where = f"handling an update in a {update.effective_chat.type} chat"
        if isinstance(error, NetworkError) and not isinstance(error, Conflict):
            LOGGER.warning("Network problem (will retry): %s: %s", type(error).__name__, error)
            return
        LOGGER.error("Unhandled error while %s", where, exc_info=error)
        await notifier.ReportFailure(where, error)

    return OnError


def MakePostShutdown(service: SocialService):
    async def PostShutdown(application: Application) -> None:
        fetcher = getattr(service, "fetcher", None)
        if fetcher is not None:
            await fetcher.Close()

    return PostShutdown


def BuildApplication(
    config: Config, store: UserStore, registry: CommandRegistry, service: SocialService, restartInfo: str = ""
) -> Application:
    builder = ApplicationBuilder().token(config.botToken).concurrent_updates(True).read_timeout(30).write_timeout(UPLOAD_TIMEOUT)
    application = builder.build()
    secrets = [config.botToken] + [v for a in config.accounts.values() for v in (a.sessionId, a.authToken)]
    notifier = Notifier(application.bot, store, secrets)
    application.post_init = MakePostInit(notifier, restartInfo)
    application.post_shutdown = MakePostShutdown(service)
    application.add_error_handler(MakeErrorHandler(notifier))
    # Commands work in private chats only (they expose the user list).
    for name in registry.Names():
        application.add_handler(CommandHandler(name, MakeCommandCallback(registry, store, name, notifier), filters=PRIVATE))
    # Everything else: social links, mentions, strangers, unknown commands.
    application.add_handler(MessageHandler(filters.ALL, MakeMessageCallback(store, service, notifier)))
    return application


class RedactingFilter(logging.Filter):
    """Replace secrets (bot token, session cookies) in every log line and traceback.

    python-telegram-bot puts the full bot token into some error messages, so this is not optional.
    """

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [secret for secret in secrets if secret]

    def Scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.Scrub(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.Scrub(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = self.Scrub(record.exc_text)
        return True


def RedactSecrets(secrets: list[str]) -> None:
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter(secrets))


def ConfigureLogging() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs full request URLs, which contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
