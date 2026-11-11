"""Thin python-telegram-bot adapter around the CommandRegistry and the social downloader."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message, MessageEntity, Update
from telegram.constants import ChatAction, ChatType
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from minionbot.builtin import SOCIAL_USAGE, URL_USAGE
from minionbot.commands import UNKNOWN_COMMAND_TEXT, CommandRegistry
from minionbot.config import Config
from minionbot.control import BotControl
from minionbot.notify import Notifier
from minionbot.social.format import BuildTexts
from minionbot.social.format import ChunkText
from minionbot.social.links import FindLinks
from minionbot.social.models import PHOTO, DownloadError, LocalMedia, Post, UnexpectedDownloadError
from minionbot.social.service import SocialService
from minionbot.social.triggers import IGNORE, NO_LINKS, Decide, Incoming
from minionbot.web.errors import UnexpectedWebError, WebError
from minionbot.web.service import WebResult, WebService
from minionbot.web.urls import FindWebUrls
from minionbot.users import SOCIAL_LINKS_MIN_ROLE, UserStore

LOGGER = logging.getLogger(__name__)

PRIVATE = filters.ChatType.PRIVATE
PRIVACY_ON_KEY = "groupPrivacyOn"
ALBUM_SIZE = 10
NO_LINKS_TEXT = "I couldn't find a supported link (a web page, or an Instagram, Facebook, Twitter/X or Threads post)."
PRIVACY_HINT_TEXT = (
    "I couldn't find a supported link. If you replied to a message, Telegram didn't pass it to me because my "
    "group privacy mode is on. My owner can fix it: @BotFather /setprivacy -> Disable, then re-add me to the group. "
    "Meanwhile you can send the link together with my @name."
)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
UPLOAD_TIMEOUT = 180


def MakeCommandCallback(
    registry: CommandRegistry, store: UserStore, name: str, notifier: Notifier | None = None, control: BotControl | None = None
):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, message = update.effective_user, update.effective_message
        if user is None or message is None:
            return
        errors: list[Exception] = []
        reply = registry.Dispatch(name, list(context.args or []), user.id, user.username, store, onError=errors.append, control=control)
        await message.reply_text(reply)
        if control is not None and control.requested:
            LOGGER.warning("Stopping the bot on the owner's request: %s", control.requested)
            context.application.stop_running()
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


async def ProcessLinks(message: Message, chat, links: list, service: SocialService, notifier: Notifier | None) -> None:
    """Download each social link and reply with it, one reply per link (shared by the implicit handler and /social)."""
    for link in links:
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


def MakeSocialCommandCallback(registry: CommandRegistry, store: UserStore, service: SocialService, notifier: Notifier | None = None):
    """/social <link...>: the explicit version of the implicit link handling."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        _, denial = registry.Authorize("social", sender.id, sender.username, store)
        if denial is not None:
            await message.reply_text(denial)
            return
        incoming = ToIncoming(message)
        links = FindLinks(incoming.text, incoming.urls)
        if not links:
            await message.reply_text(SOCIAL_USAGE)
            return
        await ProcessLinks(message, chat, links, service, notifier)

    return Callback


FALLBACK_CHUNKS = 3  # messages of article text when Telegraph is unavailable
CHUNK_CHARS = 3900


def ArticleCaption(result: WebResult) -> str:
    meta = " · ".join(filter(None, [result.siteName, result.date]))
    lines = [result.title, meta, "", result.summary] if result.summary else [result.title, meta]
    if result.notes:
        lines += ["", *result.notes]
    return "\n".join(lines).strip()


async def ReplyWithArticle(message: Message, result: WebResult) -> None:
    """The reader-mode article.

    With Telegraph: just the link. Telegram turns it into a card (title, text preview, Instant View button), so
    repeating the title or summary would only be noise. Notes the card cannot show (teaser, archived copy) go above.
    Without Telegraph: the text itself as plain messages.
    """
    if result.telegraphUrl:
        text = "\n".join([*result.notes, "", result.telegraphUrl]).lstrip("\n") if result.notes else result.telegraphUrl
        await message.reply_text(text, link_preview_options=LinkPreviewOptions(url=result.telegraphUrl))
        return
    chunks = ChunkText(result.text, CHUNK_CHARS)
    await message.reply_text(ArticleCaption(result), link_preview_options=NO_PREVIEW)
    for chunk in chunks[:FALLBACK_CHUNKS]:
        await message.reply_text(chunk, link_preview_options=NO_PREVIEW)
    tail = "…\n" if len(chunks) > FALLBACK_CHUNKS else ""
    await message.reply_text(f"{tail}Source: {result.sourceUrl}", link_preview_options=NO_PREVIEW)


async def ProcessUrls(message: Message, chat, urls: list[str], webService: WebService | None, notifier: Notifier | None) -> None:
    """Read each web page and reply with it, one reply per URL (shared by the implicit handler and /url)."""
    for url in urls:
        if webService is None:
            await message.reply_text("Reading web pages is not configured.")
            return
        await chat.send_action(ChatAction.TYPING)
        try:
            await ReplyWithArticle(message, await webService.Read(url))
        except WebError as error:
            await message.reply_text(f"{error}\n{url}", link_preview_options=NO_PREVIEW)
            if isinstance(error, UnexpectedWebError) and notifier:
                await notifier.ReportFailure("reading a web page", error.__cause__ or error)
        except Exception as error:
            LOGGER.exception("Failed to reply for %s", url)
            await message.reply_text(f"Sorry, I couldn't send the result for {url}", link_preview_options=NO_PREVIEW)
            if notifier:
                await notifier.ReportFailure("sending a web page result", error)


def MakeUrlCommandCallback(registry: CommandRegistry, store: UserStore, webService: WebService | None, notifier: Notifier | None = None):
    """/url <link...>: the explicit version of the implicit web page handling."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        _, denial = registry.Authorize("url", sender.id, sender.username, store)
        if denial is not None:
            await message.reply_text(denial)
            return
        incoming = ToIncoming(message)
        urls = FindWebUrls(incoming.text, incoming.urls)
        if not urls:
            social = FindLinks(incoming.text, incoming.urls)
            await message.reply_text("That is a social network post: use /social <link>." if social else URL_USAGE)
            return
        await ProcessUrls(message, chat, urls, webService, notifier)

    return Callback


def MakeMessageCallback(store: UserStore, service: SocialService, notifier: Notifier | None = None, webService: WebService | None = None):
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
        user = store.Authenticate(sender.id, sender.username)
        if user is None or user.role < SOCIAL_LINKS_MIN_ROLE:
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
        await ProcessLinks(message, chat, decision.links, service, notifier)
        await ProcessUrls(message, chat, decision.urls, webService, notifier)

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


def MakePostShutdown(service: SocialService, webService: WebService | None = None):
    async def PostShutdown(application: Application) -> None:
        fetcher = getattr(service, "fetcher", None)
        if fetcher is not None:
            await fetcher.Close()
        if webService is not None:
            await webService.Close()

    return PostShutdown


def BuildApplication(
    config: Config,
    store: UserStore,
    registry: CommandRegistry,
    service: SocialService,
    restartInfo: str = "",
    control: BotControl | None = None,
    webService: WebService | None = None,
) -> Application:
    builder = ApplicationBuilder().token(config.botToken).concurrent_updates(True).read_timeout(30).write_timeout(UPLOAD_TIMEOUT)
    application = builder.build()
    secrets = [config.botToken, config.telegraphToken] + [v for a in config.accounts.values() for v in (a.sessionId, a.authToken)]
    notifier = Notifier(application.bot, store, secrets)
    application.post_init = MakePostInit(notifier, restartInfo)
    application.post_shutdown = MakePostShutdown(service, webService)
    application.add_error_handler(MakeErrorHandler(notifier))
    # Commands that need chat I/O are implemented here; the rest run through registry.Dispatch.
    chatCommands = {
        "social": MakeSocialCommandCallback(registry, store, service, notifier),
        "url": MakeUrlCommandCallback(registry, store, webService, notifier),
    }
    for name, command in registry.commands.items():
        callback = chatCommands[name] if command.needsChat else MakeCommandCallback(registry, store, name, notifier, control)
        # Most commands expose bot internals (user list, restart), so they are private-chat only.
        application.add_handler(CommandHandler(name, callback, filters=filters.ALL if command.inGroups else PRIVATE))
    # Everything else: social links, mentions, strangers, unknown commands.
    application.add_handler(MessageHandler(filters.ALL, MakeMessageCallback(store, service, notifier, webService)))
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
