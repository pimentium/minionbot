"""'@bot /command ...': a mention followed by a command word (Telegram only treats messages that START with / as commands)."""

import pytest
from telegram.constants import ChatType
from telegram.ext import MessageHandler

from minionbot.bot import (
    ArgsContext,
    BuildApplication,
    MakeGoogleCommandCallback,
    MakeMessageCallback,
    MakeSocialCommandCallback,
    MakeUrlCommandCallback,
    ParseMentionCommand,
)
from minionbot.builtin import ENG_USAGE, GOOGLE_USAGE, RUS_USAGE, SOCIAL_USAGE, URL_USAGE
from minionbot.config import Config
from minionbot.control import BotControl
from minionbot.users import Role, UserRef

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import BOT, TWEET, FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate, MakePost
from tests.test_google_command import FakeSearch
from tests.test_url_command import PAGE, FakeWeb

REAL_BOT = "PimentiumMinionBot"
REAL_MESSAGE = "@PimentiumMinionBot /rus https://www.tagesanzeiger.ch/zuerich-klimaanlagen-bald-ohne-baubewilligung-225030033686"
REAL_URL = "https://www.tagesanzeiger.ch/zuerich-klimaanlagen-bald-ohne-baubewilligung-225030033686"


# ---- parsing ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (REAL_MESSAGE, ("rus", REAL_URL)),
    ("@pimentiumminionbot /ENG https://example.com/a", ("eng", "https://example.com/a")),
    ("hey @PimentiumMinionBot /rus https://example.com/a please", ("rus", "https://example.com/a please")),
    ("@PimentiumMinionBot: /google python asyncio tutorial", ("google", "python asyncio tutorial")),
    ("@PimentiumMinionBot, /url https://example.com", ("url", "https://example.com")),
    ("@PimentiumMinionBot /rus", ("rus", "")),
    ("@PimentiumMinionBot /rus@PimentiumMinionBot https://example.com", ("rus", "https://example.com")),
    ("@PimentiumMinionBot\n/google multi\nline", ("google", "multi\nline")),
    ("@PimentiumMinionBot   /social   https://x.com/a/status/1  ", ("social", "https://x.com/a/status/1")),
])
def test_mention_followed_by_a_command(text, expected):
    assert ParseMentionCommand(text, REAL_BOT) == expected


@pytest.mark.parametrize("text", [
    "@PimentiumMinionBot hello /rus https://example.com",  # the command must come right after the mention
    "/rus @PimentiumMinionBot https://example.com",  # a real command: Telegram routes it itself
    "@OtherBot /rus https://example.com",
    "@PimentiumMinionBotX /rus https://example.com",  # a longer name is another account
    "mail@PimentiumMinionBot /rus https://example.com",
    "@PimentiumMinionBot rus https://example.com",  # no slash
    "just text", "",
])
def test_everything_else_is_not_a_mention_command(text):
    assert ParseMentionCommand(text, REAL_BOT) is None


def test_without_a_known_bot_name_nothing_matches():
    assert ParseMentionCommand(REAL_MESSAGE, "") is None


def test_args_context_overrides_args_and_passes_everything_else_through():
    class Real:
        bot, application, args = "bot", "app", ["old"]

    shim = ArgsContext(Real(), ["new", "args"])
    assert shim.args == ["new", "args"] and shim.bot == "bot" and shim.application == "app"


# ---- the handler -------------------------------------------------------------------------------------------------

class World:
    """The five group-capable commands wired like BuildApplication does, with fakes behind them."""

    def __init__(self, registry, store, tmp_path):
        self.web, self.search, self.service, self.notifier = FakeWeb(), FakeSearch(), FakeService(MakePost(tmp_path)), FakeNotifier()
        self.control = BotControl()
        commands = {
            "social": MakeSocialCommandCallback(registry, store, self.service, self.notifier),
            "url": MakeUrlCommandCallback(registry, store, self.web, self.notifier),
            "rus": MakeUrlCommandCallback(registry, store, self.web, self.notifier, "rus", "ru", RUS_USAGE),
            "eng": MakeUrlCommandCallback(registry, store, self.web, self.notifier, "eng", "en", ENG_USAGE),
            "google": MakeGoogleCommandCallback(registry, store, self.search, self.notifier),
        }
        self.handler = MakeMessageCallback(store, self.service, self.notifier, self.web, commands)

    async def Send(self, text, userId=OWNER_ID, chatType=ChatType.GROUP, botName=REAL_BOT, replyTo=None):
        message = FakeMessage(text, replyTo=replyTo)
        context = FakeContext()
        context.bot.username = botName
        await self.handler(FakeUpdate(message, userId, chatType=chatType), context)
        return message


@pytest.fixture
def world(registry, store, tmp_path):
    return World(registry, store, tmp_path)


async def test_rus_after_a_mention_translates_once_and_does_not_also_run_url(world):
    message = await world.Send(REAL_MESSAGE)
    assert world.web.read == [REAL_URL] and world.web.languages == ["ru"]  # exactly one request, with the language
    assert message.reply_text.await_count == 1


@pytest.mark.parametrize("command,language", [("rus", "ru"), ("eng", "en"), ("url", None)])
async def test_each_page_command_after_a_mention(world, command, language):
    await world.Send(f"@{REAL_BOT} /{command} {PAGE}?utm_source=x")
    assert world.web.read == [PAGE] and world.web.languages == [language]


async def test_social_after_a_mention(world):
    message = await world.Send(f"@{REAL_BOT} /social {TWEET}")
    assert world.service.loaded == [TWEET] and message.reply_photo.await_count == 1 and world.web.read == []


async def test_google_after_a_mention_gets_the_words_as_its_query(world):
    await world.Send(f"@{REAL_BOT} /google python   asyncio tutorial")
    assert world.search.queries == ["python asyncio tutorial"]


async def test_it_works_in_private_chats_too(world):
    await world.Send(f"@{REAL_BOT} /eng {PAGE}", chatType=ChatType.PRIVATE)
    assert world.web.languages == ["en"]


async def test_usage_is_shown_when_the_command_has_nothing_to_work_on(world):
    cases = {"rus": RUS_USAGE, "eng": ENG_USAGE, "url": URL_USAGE, "social": SOCIAL_USAGE, "google": GOOGLE_USAGE}
    for command, usage in cases.items():
        message = await world.Send(f"@{REAL_BOT} /{command}")
        message.reply_text.assert_awaited_once_with(usage)
    assert world.web.read == [] and world.search.queries == []


async def test_strangers_are_denied_once_and_nothing_runs(world, store):
    message = await world.Send(REAL_MESSAGE, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert world.web.read == []


async def test_translation_not_configured_is_reported(registry, store, tmp_path):
    world = World(registry, store, tmp_path)
    world.web.translator = None
    message = await world.Send(REAL_MESSAGE)
    assert "Gemini API key" in message.reply_text.call_args[0][0] and world.web.read == []


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_user_and_admin_roles(world, store, role):
    store.Add(UserRef(telegramId=77), role=role)
    await world.Send(REAL_MESSAGE, userId=77)
    assert world.web.languages == ["ru"]


# ---- what must NOT change ------------------------------------------------------------------------------------------

async def test_a_plain_mention_with_a_link_is_still_the_implicit_url_handling(world):
    await world.Send(f"@{REAL_BOT} {PAGE}")
    assert world.web.languages == [None]


async def test_an_unknown_command_word_falls_back_to_the_implicit_handling(world):
    await world.Send(f"@{REAL_BOT} /foo {PAGE}")
    assert world.web.languages == [None]


async def test_private_only_commands_cannot_be_run_through_a_mention(world, store):
    users = len(store.List())
    message = await world.Send(f"@{REAL_BOT} /users add 12345 admin")
    await world.Send(f"@{REAL_BOT} /restart")
    await world.Send(f"@{REAL_BOT} /shutdown")
    assert len(store.List()) == users and world.control.requested is None
    assert "supported link" in message.reply_text.call_args[0][0]  # treated as a mention without a link


async def test_other_bots_and_commands_before_the_mention_are_ignored(world):
    await world.Send(f"@SomeoneElseBot /rus {PAGE}")
    await world.Send(f"hello /rus @{REAL_BOT}")
    assert world.web.read == []


async def test_a_group_message_without_a_mention_is_still_ignored(world):
    await world.Send(f"/rus {PAGE}")  # a real command is handled by the CommandHandler, not by this catch-all
    assert world.web.read == []


# ---- replying with a command -------------------------------------------------------------------------------------------

async def test_mention_command_as_a_reply_uses_the_link_of_the_replied_message(world):
    original = FakeMessage(f"interesting {PAGE}")
    await world.Send(f"@{REAL_BOT} /rus", replyTo=original)
    assert world.web.read == [PAGE] and world.web.languages == ["ru"]


async def test_own_link_wins_over_the_replied_one(world):
    original = FakeMessage("look https://old.example.com/a")
    await world.Send(f"@{REAL_BOT} /eng {PAGE}", replyTo=original)
    assert world.web.read == [PAGE]


async def test_social_command_reads_the_replied_message_too(world):
    await world.Send(f"@{REAL_BOT} /social", replyTo=FakeMessage(f"see {TWEET}"))
    assert world.service.loaded == [TWEET]


async def test_a_reply_without_any_link_gets_the_usage(world):
    message = await world.Send(f"@{REAL_BOT} /rus", replyTo=FakeMessage("no link here"))
    message.reply_text.assert_awaited_once_with(RUS_USAGE)


async def test_a_replied_social_post_is_pointed_to_social(world):
    message = await world.Send(f"@{REAL_BOT} /url", replyTo=FakeMessage(f"see {TWEET}"))
    assert "/social" in message.reply_text.call_args[0][0]


async def test_plain_slash_commands_also_use_the_replied_message(registry, store):
    web = FakeWeb()
    callback = MakeUrlCommandCallback(registry, store, web, None, "rus", "ru", RUS_USAGE)
    message = FakeMessage("/rus", replyTo=FakeMessage(f"read {PAGE}"))
    await callback(FakeUpdate(message), FakeContext([]))
    assert web.read == [PAGE] and web.languages == ["ru"]


# ---- wiring ----------------------------------------------------------------------------------------------------------------

async def test_build_application_wires_the_group_commands_into_the_catch_all_handler(registry, store):
    web, search = FakeWeb(), FakeSearch()
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService(), webService=web, searchClient=search)
    handler = app.handlers[0][-1]
    assert isinstance(handler, MessageHandler)
    for text, check in ((REAL_MESSAGE, lambda: web.languages == ["ru"]), (f"@{REAL_BOT} /google hello world", lambda: search.queries == ["hello world"])):
        context = FakeContext()
        context.bot.username = REAL_BOT
        await handler.callback(FakeUpdate(FakeMessage(text), chatType=ChatType.SUPERGROUP), context)
        assert check()
