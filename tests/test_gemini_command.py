"""/gemini <prompt>: everything after the command goes to Gemini, the answer comes back formatted for Telegram."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import CommandHandler

from minionbot.bot import (
    GEMINI_MAX_PROMPT_CHARS,
    BuildApplication,
    MakeGeminiCommandCallback,
    MakeMessageCallback,
    PromptText,
)
from minionbot.builtin import GEMINI_USAGE
from minionbot.config import Config
from minionbot.users import Role, UserRef
from minionbot.web.errors import TranslationError, TranslationServiceError

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate

REAL_BOT = "PimentiumMinionBot"


class FakeGemini:
    def __init__(self, answer="Hello **world**", error=None):
        self.answer, self.error, self.prompts, self.media = answer, error, [], []

    async def Ask(self, prompt, media=()):
        self.prompts.append(prompt)
        self.media.append(list(media))
        if self.error:
            raise self.error
        return self.answer


async def Run(registry, store, text, gemini, userId=OWNER_ID, chatType=ChatType.PRIVATE, notifier=None, replyTo=None, quote=None):
    message = FakeMessage(text, replyTo=replyTo)
    if quote is not None:
        message.quote = MagicMock(text=quote)
    update = FakeUpdate(message, userId, chatType=chatType)
    await MakeGeminiCommandCallback(registry, store, gemini, notifier)(update, FakeContext([]))
    return message, update


def Sent(message):
    return [call[0][0] for call in message.reply_text.call_args_list]


# ---- the prompt ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,prompt", [
    ("/gemini what is python", "what is python"),
    ("/gemini   spaced   out   prompt  ", "spaced   out   prompt"),
    (f"/gemini@{REAL_BOT} hello there", "hello there"),
    ("/gemini first line\nsecond line\n\nthird", "first line\nsecond line\n\nthird"),
    ("/gemini\nprompt on the next line", "prompt on the next line"),
    ("/gemini", ""), ("/gemini   ", ""),
])
def test_prompt_is_the_text_after_the_command_exactly(text, prompt):
    assert PromptText(FakeMessage(text), object()) == prompt


def test_prompt_after_a_mention_comes_from_the_raw_arguments():
    class Context:
        rawArgs = "multi\nline   prompt"

    assert PromptText(FakeMessage("@Bot /gemini ignored"), Context()) == "multi\nline   prompt"


# ---- behaviour --------------------------------------------------------------------------------------------------------

async def test_sends_the_prompt_and_returns_the_answer_as_telegram_html(registry, store):
    gemini = FakeGemini("Hello **world**\n\n- item")
    message, update = await Run(registry, store, "/gemini say hello", gemini)
    assert gemini.prompts == ["say hello"]
    message.reply_text.assert_awaited_once()
    assert Sent(message) == ["Hello <b>world</b>\n\n• item"]
    assert message.reply_text.call_args[1]["parse_mode"] == ParseMode.HTML and message.reply_text.call_args[1]["link_preview_options"].is_disabled
    update.effective_chat.send_action.assert_awaited_once_with(ChatAction.TYPING)


async def test_a_long_answer_is_split_into_several_messages(registry, store):
    answer = "\n".join(f"- item {i} " + "word " * 15 for i in range(120))
    message, _ = await Run(registry, store, "/gemini list things", FakeGemini(answer))
    assert message.reply_text.await_count > 2 and all(len(t) <= 4096 for t in Sent(message))
    assert "item 0" in Sent(message)[0] and "item 119" in Sent(message)[-1]


async def test_telegram_refusing_the_html_falls_back_to_plain_text(registry, store):
    message = FakeMessage("/gemini hi")
    message.reply_text = AsyncMock(side_effect=[BadRequest("Can't parse entities"), None])
    await MakeGeminiCommandCallback(registry, store, FakeGemini("Some **answer**"))(FakeUpdate(message), FakeContext([]))
    first, second = message.reply_text.call_args_list
    assert first[1]["parse_mode"] == ParseMode.HTML and "parse_mode" not in second[1] and second[0][0] == "Some **answer**"


async def test_usage_without_a_prompt_and_overlong_prompts(registry, store):
    gemini = FakeGemini()
    message, _ = await Run(registry, store, "/gemini", gemini)
    message.reply_text.assert_awaited_once_with(GEMINI_USAGE)
    message, _ = await Run(registry, store, "/gemini " + "x" * (GEMINI_MAX_PROMPT_CHARS + 1), gemini)
    assert "too long" in Sent(message)[0] and gemini.prompts == []


async def test_not_configured(registry, store):
    message, _ = await Run(registry, store, "/gemini hi", None)
    assert "Gemini API key" in Sent(message)[0]


async def test_unknown_role_is_denied_and_nothing_is_sent_to_gemini(registry, store):
    gemini = FakeGemini()
    message, _ = await Run(registry, store, "/gemini hi", gemini, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert gemini.prompts == []


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_user_and_admin_may_ask_also_in_groups(registry, store, role):
    store.Add(UserRef(telegramId=71), role=role)
    gemini = FakeGemini()
    await Run(registry, store, "/gemini hi", gemini, userId=71, chatType=ChatType.SUPERGROUP)
    assert gemini.prompts == ["hi"]


async def test_plain_gemini_errors_are_shown_but_only_service_problems_notify(registry, store):
    notifier = FakeNotifier()
    message, _ = await Run(registry, store, "/gemini hi", FakeGemini(error=TranslationError("The Gemini service is busy (free-tier limit); try again in a minute.")), notifier=notifier)
    assert Sent(message) == ["The Gemini service is busy (free-tier limit); try again in a minute."]
    notifier.ReportFailure.assert_not_awaited()
    error = TranslationServiceError("The Gemini service refused the API key.")
    message, _ = await Run(registry, store, "/gemini hi", FakeGemini(error=error), notifier=notifier)
    assert Sent(message) == ["The Gemini service refused the API key."] and notifier.ReportFailure.call_args[0][1] is error


async def test_unexpected_failures_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    bug = ValueError("bug")
    message, _ = await Run(registry, store, "/gemini hi", FakeGemini(error=bug), notifier=notifier)
    assert "went wrong" in Sent(message)[0] and notifier.ReportFailure.call_args[0][1] is bug


async def test_updates_without_a_message_are_ignored(registry, store):
    from unittest.mock import MagicMock

    update = MagicMock()
    update.effective_message = None
    await MakeGeminiCommandCallback(registry, store, FakeGemini())(update, FakeContext([]))


# ---- after a mention + wiring -----------------------------------------------------------------------------------------------

async def test_gemini_after_a_mention_keeps_the_prompt_text_including_line_breaks(registry, store):
    gemini = FakeGemini()
    handler = MakeMessageCallback(store, FakeService(), None, None, {"gemini": MakeGeminiCommandCallback(registry, store, gemini)})
    message = FakeMessage(f"@{REAL_BOT} /gemini explain this:\n\nline one\n  line two")
    context = FakeContext()
    context.bot.username = REAL_BOT
    await handler(FakeUpdate(message, chatType=ChatType.GROUP), context)
    assert gemini.prompts == ["explain this:\n\nline one\n  line two"] and message.reply_text.await_count == 1


async def test_build_application_wires_gemini_for_commands_and_mentions(registry, store):
    gemini = FakeGemini()
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService(), gemini=gemini)
    assert "gemini" in {name for h in app.handlers[0] if isinstance(h, CommandHandler) for name in h.commands}
    assert "/gemini <prompt>" in registry.Dispatch("help", [], OWNER_ID, None, store)
    message = FakeMessage(f"@{REAL_BOT} /gemini hello")
    context = FakeContext()
    context.bot.username = REAL_BOT
    await app.handlers[0][-1].callback(FakeUpdate(message, chatType=ChatType.GROUP), context)
    assert gemini.prompts == ["hello"]


# ---- replies: the quoted text and the media of the replied-to message go to Gemini too ---------------------------------

def Replied(text=None, caption=None, **media):
    """A replied-to message; media kwargs: photo (list of sizes), video/animation/video_note/document items."""
    replied = FakeMessage(text)
    replied.caption = caption
    for name in ("photo", "video", "animation", "video_note", "document"):
        setattr(replied, name, media.get(name))
    return replied


def MediaItem(data=b"BYTES", mime=None, size=None, error=None, **extra):
    item = MagicMock()
    item.file_id, item.mime_type, item.file_size = "id", mime, size if size is not None else len(data)
    for key, value in extra.items():
        setattr(item, key, value)
    file = MagicMock()
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data), side_effect=error)
    item.get_file = AsyncMock(return_value=file)
    return item


async def test_the_replied_to_text_is_added_at_the_end_of_the_prompt(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini summarize", gemini, replyTo=Replied("The original\ntext"))
    assert gemini.prompts == ["summarize\n\nThe message I am replying to:\nThe original\ntext"] and gemini.media == [[]]


async def test_a_selected_quote_wins_over_the_whole_replied_to_message(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini explain", gemini, replyTo=Replied("whole message"), quote="just this part")
    assert gemini.prompts == ["explain\n\nThe message I am replying to:\njust this part"]


async def test_a_caption_counts_as_the_replied_to_text(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini what", gemini, replyTo=Replied(None, caption="holiday photo"))
    assert gemini.prompts == ["what\n\nThe message I am replying to:\nholiday photo"]


async def test_a_reply_with_an_empty_prompt_still_asks_about_the_quoted_text(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini", gemini, replyTo=Replied("translate me"))
    assert gemini.prompts == ["The message I am replying to:\ntranslate me"]


async def test_without_a_reply_nothing_is_added(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini hello", gemini)
    assert gemini.prompts == ["hello"] and gemini.media == [[]]


async def test_a_long_quoted_text_is_cut_but_does_not_make_the_prompt_too_long(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini tl;dr", gemini, replyTo=Replied("x" * (GEMINI_MAX_PROMPT_CHARS + 500)))
    assert gemini.prompts[0].count("x") == GEMINI_MAX_PROMPT_CHARS


async def test_the_largest_photo_of_the_replied_to_message_is_sent(registry, store):
    small, large = MediaItem(b"small", size=5, width=90), MediaItem(b"LARGE", size=50, width=1280)
    gemini = FakeGemini()
    await Run(registry, store, "/gemini what is this", gemini, replyTo=Replied(photo=[small, large]))
    assert gemini.media == [[("image/jpeg", b"LARGE")]] and gemini.prompts == ["what is this"]


async def test_a_picture_alone_is_described(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini", gemini, replyTo=Replied(photo=[MediaItem(b"P")]))
    assert gemini.media == [[("image/jpeg", b"P")]] and gemini.prompts == ["What is in this?"]


@pytest.mark.parametrize("kind,mime,expected", [
    ("video", "video/mp4", "video/mp4"), ("video", None, "video/mp4"), ("animation", "video/mp4", "video/mp4"),
    ("video_note", None, "video/mp4"), ("document", "image/png", "image/png"), ("video", "video/webm", "video/webm"),
])
async def test_videos_animations_and_image_documents_are_sent(registry, store, kind, mime, expected):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini look", gemini, replyTo=Replied(**{kind: MediaItem(b"V", mime=mime)}))
    assert gemini.media == [[(expected, b"V")]]


async def test_text_and_media_of_the_same_message_are_both_sent(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini what", gemini, replyTo=Replied(caption="cap", photo=[MediaItem(b"P")]))
    assert gemini.prompts == ["what\n\nThe message I am replying to:\ncap"] and gemini.media == [[("image/jpeg", b"P")]]


async def test_other_documents_are_ignored(registry, store):
    gemini = FakeGemini()
    await Run(registry, store, "/gemini what", gemini, replyTo=Replied(document=MediaItem(b"%PDF", mime="application/pdf")))
    assert gemini.prompts == ["what"] and gemini.media == [[]]


async def test_a_too_big_file_is_refused_without_calling_gemini(registry, store):
    gemini = FakeGemini()
    big = MediaItem(b"x", mime="video/mp4", size=30 * 1024 * 1024)
    message, _ = await Run(registry, store, "/gemini what", gemini, replyTo=Replied(video=big))
    assert gemini.prompts == [] and "too big" in Sent(message)[0]
    big.get_file.assert_not_awaited()


async def test_a_failed_download_is_reported_without_calling_gemini(registry, store):
    gemini = FakeGemini()
    message, _ = await Run(registry, store, "/gemini what", gemini, replyTo=Replied(video=MediaItem(mime="video/mp4", error=TelegramError("File is too big"))))
    assert gemini.prompts == [] and "couldn't download" in Sent(message)[0]


async def test_an_unreadable_mime_type_is_refused(registry, store):
    gemini = FakeGemini()
    message, _ = await Run(registry, store, "/gemini what", gemini, replyTo=Replied(video=MediaItem(mime="video/x-weird")))
    assert gemini.prompts == [] and "can't read" in Sent(message)[0]


async def test_usage_when_there_is_no_prompt_and_nothing_to_reply_to(registry, store):
    message, _ = await Run(registry, store, "/gemini", FakeGemini(), replyTo=Replied(None))
    assert Sent(message) == [GEMINI_USAGE]


async def test_strangers_cannot_make_the_bot_download_media(registry, store):
    gemini, item = FakeGemini(), MediaItem()
    await Run(registry, store, "/gemini what", gemini, userId=STRANGER_ID, replyTo=Replied(video=item))
    assert gemini.prompts == [] and item.get_file.await_count == 0
