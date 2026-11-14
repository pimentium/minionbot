"""/gemini <prompt>: everything after the command goes to Gemini, the answer comes back formatted for Telegram."""

from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest
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
        self.answer, self.error, self.prompts = answer, error, []

    async def Ask(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer


async def Run(registry, store, text, gemini, userId=OWNER_ID, chatType=ChatType.PRIVATE, notifier=None):
    message = FakeMessage(text)
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
