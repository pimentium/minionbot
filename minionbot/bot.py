"""Thin python-telegram-bot adapter around the CommandRegistry and the social downloader."""

from __future__ import annotations

import html
import logging
import re
import tempfile
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message, MessageEntity, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from minionbot.ai.markdown import SplitMarkdown, ToHtml
from minionbot.builtin import ENG_USAGE, GEMINI_USAGE, GOOGLE_USAGE, RUS_USAGE, SOCIAL_USAGE, URL_USAGE
from minionbot.commands import UNKNOWN_COMMAND_TEXT, CommandRegistry
from minionbot.config import Config
from minionbot.control import BotControl
from minionbot.notify import Notifier
from minionbot.search.format import FormatResult
from minionbot.search.models import SearchError, SearchServiceError, UnexpectedSearchError
from minionbot.search.serpapi import MAX_QUERY_CHARS, SerpApiClient
from minionbot.social.format import BuildTexts
from minionbot.social.format import ChunkText
from minionbot.social.links import FindLinks
from minionbot.social.models import PHOTO, DownloadError, LocalMedia, Post, UnexpectedDownloadError
from minionbot.social.service import SocialService
from minionbot.social.triggers import IGNORE, NO_LINKS, Decide, Incoming
from minionbot.translate.gemini import GeminiClient
from minionbot.translate.language import LANGUAGE_NAMES, IsAlready
from minionbot.translate.text import TranslateText
from minionbot.web.errors import TranslationError, TranslationServiceError, UnexpectedWebError, WebError
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


HTML_TAG = re.compile(r"<[^>]*>")


def SplitHtmlBlocks(text: str, limit: int = 3900) -> list[str]:
    """Split HTML text between blank lines (never inside a tag pair) into messages of at most `limit` characters."""
    messages: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        if current and len(current) + 2 + len(block) > limit:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        messages.append(current)
    return messages


async def ReplyWithHtml(message: Message, text: str) -> None:
    """A reply written in Telegram HTML; if Telegram refuses the markup, the same text without it."""
    for part in SplitHtmlBlocks(text):
        try:
            await message.reply_text(part, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        except BadRequest as error:
            LOGGER.info("Telegram refused the HTML (%s); sending plain text", error)
            await message.reply_text(html.unescape(HTML_TAG.sub("", part)), link_preview_options=NO_PREVIEW)


def MakeCommandCallback(
    registry: CommandRegistry, store: UserStore, name: str, notifier: Notifier | None = None, control: BotControl | None = None
):
    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, message = update.effective_user, update.effective_message
        if user is None or message is None:
            return
        errors: list[Exception] = []
        reply = registry.Dispatch(name, list(context.args or []), user.id, user.username, store, onError=errors.append, control=control)
        if registry.commands[name].html:
            await ReplyWithHtml(message, reply)
        else:
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


def CommandSources(message: Message) -> list[Incoming]:
    """Where a command looks for links: its own text first, then the message it replies to."""
    replied = ReplySource(message)
    return [ToIncoming(message)] + ([replied] if replied else [])


def MentionCommandPattern(botUsername: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@])@{re.escape(botUsername)}\b[\s,:;-]*/(\w+)(?:@{re.escape(botUsername)})?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def ParseMentionCommand(text: str, botUsername: str) -> tuple[str, str] | None:
    """'@bot /rus <link>' -> ('rus', '<link>'). Telegram only treats a message that STARTS with / as a command, so
    this form (natural in groups, where you address the bot by name) is recognised here."""
    match = MentionCommandPattern(botUsername).search(text or "") if botUsername else None
    return (match.group(1).lower(), (match.group(2) or "").strip()) if match else None


class ArgsContext:
    """The real context, but with the arguments of the command that followed the mention."""

    def __init__(self, context, args: list[str], rawArgs: str | None = None):
        self.wrapped, self.args, self.rawArgs = context, args, rawArgs

    def __getattr__(self, name: str):
        return getattr(self.wrapped, name)


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
        links = next((found for source in CommandSources(message) if (found := FindLinks(source.text, source.urls))), [])
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


async def ProcessUrls(
    message: Message, chat, urls: list[str], webService: WebService | None, notifier: Notifier | None, language: str | None = None
) -> None:
    """Read each web page and reply with it, one reply per URL (shared by the implicit handler and /url)."""
    for url in urls:
        if webService is None:
            await message.reply_text("Reading web pages is not configured.")
            return
        await chat.send_action(ChatAction.TYPING)
        try:
            await ReplyWithArticle(message, await webService.Read(url, language))
        except WebError as error:
            await message.reply_text(f"{error}\n{url}", link_preview_options=NO_PREVIEW)
            if isinstance(error, UnexpectedWebError) and notifier:
                await notifier.ReportFailure("reading a web page", error.__cause__ or error)
            elif isinstance(error, TranslationServiceError) and notifier:
                await notifier.ReportFailure("translating a web page (Gemini)", error)
        except Exception as error:
            LOGGER.exception("Failed to reply for %s", url)
            await message.reply_text(f"Sorry, I couldn't send the result for {url}", link_preview_options=NO_PREVIEW)
            if notifier:
                await notifier.ReportFailure("sending a web page result", error)


async def ProcessText(message: Message, chat, text: str, translator, notifier: Notifier | None, language: str) -> None:
    """/rus /eng with text instead of a link: reply with the translation as plain messages."""
    name = LANGUAGE_NAMES[language]
    if IsAlready(text, language):
        await message.reply_text(f"The text is already in {name}, so it was not translated.")
        return
    await chat.send_action(ChatAction.TYPING)
    try:
        translated = await TranslateText(text, translator, language)
    except TranslationError as error:
        await message.reply_text(str(error))
        if isinstance(error, TranslationServiceError) and notifier:
            await notifier.ReportFailure("translating text (Gemini)", error)
        return
    except Exception as error:
        LOGGER.exception("Failed to translate text")
        await message.reply_text("Sorry, something went wrong while translating.")
        if notifier:
            await notifier.ReportFailure("translating text (Gemini)", error)
        return
    for chunk in ChunkText(translated, CHUNK_CHARS):
        await message.reply_text(chunk, link_preview_options=NO_PREVIEW)


def MakeUrlCommandCallback(
    registry: CommandRegistry,
    store: UserStore,
    webService: WebService | None,
    notifier: Notifier | None = None,
    name: str = "url",
    language: str | None = None,
    usage: str = URL_USAGE,
):
    """/url <link...> (and /rus, /eng: the same, translated first): the explicit version of the web page handling."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        _, denial = registry.Authorize(name, sender.id, sender.username, store)
        if denial is not None:
            await message.reply_text(denial)
            return
        sources = [source for source in (ToIncoming(message), ReplySource(message)) if source is not None]
        ownText = PromptText(message, context) if language else ""  # /rus /eng also translate plain text
        replyText = ReplyQuote(message) if language else ""
        # Order: a link in the command; text given to the command; a link in the replied-to message; its text.
        urls = FindWebUrls(sources[0].text, sources[0].urls)
        if not urls and not ownText:
            urls = next((found for source in sources[1:] if (found := FindWebUrls(source.text, source.urls))), [])
        if not urls:
            if any(FindLinks(source.text, source.urls) for source in sources):
                await message.reply_text("That is a social network post: use /social <link>.")
                return
            text = ownText or replyText
            if not text:
                await message.reply_text(usage)
                return
            if webService is None or webService.translator is None:
                await message.reply_text("Translation is not configured (the owner needs to set a Gemini API key).")
                return
            await ProcessText(message, chat, text, webService.translator, notifier, language)
            return
        if language and (webService is None or webService.translator is None):
            await message.reply_text("Translation is not configured (the owner needs to set a Gemini API key).")
            return
        await ProcessUrls(message, chat, urls, webService, notifier, language)

    return Callback


def MakeGoogleCommandCallback(registry: CommandRegistry, store: UserStore, searchClient: SerpApiClient | None, notifier: Notifier | None = None):
    """/google <query>: AI overview (if Google shows one) and the top organic results."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        _, denial = registry.Authorize("google", sender.id, sender.username, store)
        if denial is not None:
            await message.reply_text(denial)
            return
        query = " ".join(context.args or [])
        if not query.strip():
            await message.reply_text(GOOGLE_USAGE)
            return
        if len(query) > MAX_QUERY_CHARS:
            await message.reply_text(f"That query is too long (at most {MAX_QUERY_CHARS} characters).")
            return
        if searchClient is None:
            await message.reply_text("Google search is not configured (the owner needs to set a SerpApi key).")
            return
        await chat.send_action(ChatAction.TYPING)
        try:
            for text in FormatResult(await searchClient.Search(query)):
                await message.reply_text(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        except SearchError as error:
            await message.reply_text(str(error))
            if isinstance(error, SearchServiceError) and notifier:
                await notifier.ReportFailure("Google search (search service)", error)
            elif isinstance(error, UnexpectedSearchError) and notifier:
                await notifier.ReportFailure("Google search", error.__cause__ or error)
        except Exception as error:
            LOGGER.exception("Failed to answer /google")
            await message.reply_text("Sorry, I couldn't send the search results.")
            if notifier:
                await notifier.ReportFailure("sending Google results", error)

    return Callback


GEMINI_MAX_PROMPT_CHARS = 8000
COMMAND_PREFIX = re.compile(r"^\s*/\w+(?:@\w+)?[ \t]*\n?")


def PromptText(message: Message, context) -> str:
    """Everything after the command, exactly as typed (line breaks kept)."""
    raw = getattr(context, "rawArgs", None)  # set (by ArgsContext) when the command followed a mention
    if isinstance(raw, str):
        return raw.strip()
    return COMMAND_PREFIX.sub("", message.text or message.caption or "", count=1).strip()


QUOTE_HEADER = "The message I am replying to:"
# Gemini takes at most ~20 MB per request including everything inline; stay below it. Telegram bots can download 20 MB.
GEMINI_MAX_MEDIA_BYTES = 15 * 1024 * 1024
GEMINI_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
GEMINI_VIDEO_TYPES = {"video/mp4", "video/mpeg", "video/quicktime", "video/webm", "video/x-flv", "video/3gpp", "video/x-ms-wmv"}


class ReplyMediaError(Exception):
    """The replied-to media can't be sent to Gemini; the text is what the user is told."""


def _FileLike(value) -> bool:
    return isinstance(getattr(value, "file_id", None), str)


def ReplyQuote(message: Message) -> str:
    """What the user replied to: the part they selected (a Telegram quote), else the whole text/caption."""
    quote = getattr(message, "quote", None)
    quoted = getattr(quote, "text", None)
    if isinstance(quoted, str) and quoted.strip():
        return quoted.strip()
    replied = getattr(message, "reply_to_message", None)
    if replied is None:
        return ""
    text = getattr(replied, "text", None) or getattr(replied, "caption", None)
    return text.strip() if isinstance(text, str) else ""


def ReplyMediaFile(message: Message):
    """(file object, mime type) of the picture/video in the replied-to message, or None."""
    replied = getattr(message, "reply_to_message", None)
    if replied is None:
        return None
    photo = getattr(replied, "photo", None)
    if isinstance(photo, (tuple, list)) and photo and all(_FileLike(size) for size in photo):
        return max(photo, key=lambda size: (getattr(size, "file_size", None) or 0, getattr(size, "width", 0) or 0)), "image/jpeg"
    for name, fallback in (("video", "video/mp4"), ("animation", "video/mp4"), ("video_note", "video/mp4"), ("document", "")):
        item = getattr(replied, name, None)
        if not _FileLike(item):
            continue
        mime = getattr(item, "mime_type", None)
        mime = mime if isinstance(mime, str) and mime else fallback
        if name == "document" and mime not in GEMINI_IMAGE_TYPES | GEMINI_VIDEO_TYPES:
            continue  # other documents (PDF, archives...) are not sent
        return item, mime
    return None


async def LoadReplyMedia(message: Message) -> list[tuple[str, bytes]]:
    """Download the replied-to picture/video for Gemini. Raises ReplyMediaError with a user-facing text."""
    found = ReplyMediaFile(message)
    if found is None:
        return []
    item, mime = found
    if mime not in GEMINI_IMAGE_TYPES | GEMINI_VIDEO_TYPES:
        raise ReplyMediaError(f"Gemini can't read this kind of file ({mime}).")
    size = getattr(item, "file_size", None)
    if isinstance(size, int) and size > GEMINI_MAX_MEDIA_BYTES:
        raise ReplyMediaError("That file is too big to send to Gemini (at most 15 MB).")
    try:
        file = await item.get_file()
        data = bytes(await file.download_as_bytearray())
    except TelegramError as error:
        LOGGER.info("Could not download the replied-to media: %s", error)
        raise ReplyMediaError("I couldn't download the file you replied to.") from error
    if len(data) > GEMINI_MAX_MEDIA_BYTES:
        raise ReplyMediaError("That file is too big to send to Gemini (at most 15 MB).")
    return [(mime, data)]


def BuildGeminiPrompt(prompt: str, quote: str) -> str:
    """The user's prompt, then (at the end) the message it replies to."""
    if not quote:
        return prompt
    return f"{prompt}\n\n{QUOTE_HEADER}\n{quote}" if prompt else f"{QUOTE_HEADER}\n{quote}"


async def ReplyWithMarkdown(message: Message, markdown: str) -> None:
    """Gemini's Markdown as Telegram HTML; if Telegram refuses the formatting, as plain text."""
    for chunk in SplitMarkdown(markdown):
        try:
            await message.reply_text(ToHtml(chunk), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        except BadRequest as error:
            LOGGER.info("Telegram refused the HTML formatting (%s); sending plain text", error)
            await message.reply_text(chunk, link_preview_options=NO_PREVIEW)


def MakeGeminiCommandCallback(registry: CommandRegistry, store: UserStore, gemini: GeminiClient | None, notifier: Notifier | None = None):
    """/gemini <prompt>: the text after the command goes to Gemini as it is; its answer comes back."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        _, denial = registry.Authorize("gemini", sender.id, sender.username, store)
        if denial is not None:
            await message.reply_text(denial)
            return
        prompt = PromptText(message, context)
        quote = ReplyQuote(message)
        hasMedia = ReplyMediaFile(message) is not None
        if not prompt and not quote and not hasMedia:
            await message.reply_text(GEMINI_USAGE)
            return
        if len(prompt) > GEMINI_MAX_PROMPT_CHARS:
            await message.reply_text(f"That prompt is too long (at most {GEMINI_MAX_PROMPT_CHARS} characters).")
            return
        if gemini is None:
            await message.reply_text("Gemini is not configured (the owner needs to set a Gemini API key).")
            return
        await chat.send_action(ChatAction.TYPING)
        try:
            media = await LoadReplyMedia(message)
        except ReplyMediaError as error:
            await message.reply_text(str(error))
            return
        # The quoted text is capped separately, so a long message can't make an otherwise fine prompt "too long".
        fullPrompt = BuildGeminiPrompt(prompt, quote[:GEMINI_MAX_PROMPT_CHARS])
        if not fullPrompt:  # only a picture/video: ask what it shows
            fullPrompt = "What is in this?"
        try:
            await ReplyWithMarkdown(message, await gemini.Ask(fullPrompt, media))
        except TranslationError as error:
            await message.reply_text(str(error))
            if isinstance(error, TranslationServiceError) and notifier:
                await notifier.ReportFailure("Gemini (/gemini)", error)
        except Exception as error:
            LOGGER.exception("Failed to answer /gemini")
            await message.reply_text("Sorry, something went wrong while asking Gemini.")
            if notifier:
                await notifier.ReportFailure("Gemini (/gemini)", error)

    return Callback


def MakeMessageCallback(
    store: UserStore,
    service: SocialService,
    notifier: Notifier | None = None,
    webService: WebService | None = None,
    mentionCommands: dict | None = None,
):
    """mentionCommands: callbacks of the commands that also work as '@bot /command ...' (those allowed in groups)."""

    async def Callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, sender, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or sender is None or chat is None:
            return
        incoming = ToIncoming(message)
        command = ParseMentionCommand(incoming.text, context.bot.username or "") if mentionCommands else None
        if command and command[0] in mentionCommands:
            LOGGER.info("Message in %s chat %s from user %s: mention command /%s", chat.type, chat.id, sender.id, command[0])
            await mentionCommands[command[0]](update, ArgsContext(context, command[1].split(), command[1]))
            return
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


def MakePostShutdown(service: SocialService, webService: WebService | None = None, searchClient: SerpApiClient | None = None):
    async def PostShutdown(application: Application) -> None:
        fetcher = getattr(service, "fetcher", None)
        if fetcher is not None:
            await fetcher.Close()
        if webService is not None:
            await webService.Close()
        if searchClient is not None:
            await searchClient.Close()

    return PostShutdown


def BuildApplication(
    config: Config,
    store: UserStore,
    registry: CommandRegistry,
    service: SocialService,
    restartInfo: str = "",
    control: BotControl | None = None,
    webService: WebService | None = None,
    searchClient: SerpApiClient | None = None,
    gemini: GeminiClient | None = None,
) -> Application:
    builder = ApplicationBuilder().token(config.botToken).concurrent_updates(True).read_timeout(30).write_timeout(UPLOAD_TIMEOUT)
    application = builder.build()
    secrets = [config.botToken, config.telegraphToken, config.serpApiKey, config.geminiKey] + [v for a in config.accounts.values() for v in (a.sessionId, a.authToken)]
    notifier = Notifier(application.bot, store, secrets)
    application.post_init = MakePostInit(notifier, restartInfo)
    application.post_shutdown = MakePostShutdown(service, webService, searchClient)
    application.add_error_handler(MakeErrorHandler(notifier))
    # Commands that need chat I/O are implemented here; the rest run through registry.Dispatch.
    chatCommands = {
        "social": MakeSocialCommandCallback(registry, store, service, notifier),
        "url": MakeUrlCommandCallback(registry, store, webService, notifier),
        "rus": MakeUrlCommandCallback(registry, store, webService, notifier, "rus", "ru", RUS_USAGE),
        "eng": MakeUrlCommandCallback(registry, store, webService, notifier, "eng", "en", ENG_USAGE),
        "google": MakeGoogleCommandCallback(registry, store, searchClient, notifier),
        "gemini": MakeGeminiCommandCallback(registry, store, gemini, notifier),
    }
    for name, command in registry.commands.items():
        callback = chatCommands[name] if command.needsChat else MakeCommandCallback(registry, store, name, notifier, control)
        # Most commands expose bot internals (user list, restart), so they are private-chat only.
        application.add_handler(CommandHandler(name, callback, filters=filters.ALL if command.inGroups else PRIVATE))
    # Everything else: social links, mentions, strangers, unknown commands.
    mentionCommands = {name: chatCommands[name] for name, command in registry.commands.items() if command.needsChat and command.inGroups}
    application.add_handler(MessageHandler(filters.ALL, MakeMessageCallback(store, service, notifier, webService, mentionCommands)))
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
