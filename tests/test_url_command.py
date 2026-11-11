"""Stage 2: /url and the implicit handling of ordinary web links."""

from unittest.mock import AsyncMock

import pytest
from telegram import MessageEntity
from telegram.constants import ChatType
from telegram.ext import CommandHandler

from minionbot import bot
from minionbot.bot import BuildApplication, MakeMessageCallback, MakeUrlCommandCallback
from minionbot.builtin import URL_USAGE
from minionbot.config import Config
from minionbot.users import Role, UserRef
from minionbot.web.errors import UnexpectedWebError, UnsafeUrlError, WebError
from minionbot.web.service import WebResult

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import BOT, TWEET, FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate, MakePost

PAGE = "https://news.example.com/story"
TELEGRAPH = "https://telegra.ph/Story-11-09"


def Result(telegraph=TELEGRAPH, text="Body text. " * 50, notes=()):
    return WebResult(
        sourceUrl=PAGE, title="The Story", siteName="Example News", date="2026-11-09", summary="A short summary.",
        text=text, telegraphUrl=telegraph, notes=list(notes),
    )


class FakeWeb:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.read = result or Result(), error, []

    async def Read(self, url):
        self.read.append(url)
        if self.error:
            raise self.error
        return self.result


def Texts(message):
    return [call[0][0] for call in message.reply_text.call_args_list]


async def RunUrl(registry, store, text, web, userId=OWNER_ID, chatType=ChatType.PRIVATE, notifier=None, **kwargs):
    message = FakeMessage(text, **kwargs)
    await MakeUrlCommandCallback(registry, store, web, notifier)(FakeUpdate(message, userId, chatType=chatType), FakeContext([]))
    return message


# ---- /url ---------------------------------------------------------------------------------------------

async def test_url_replies_with_the_telegraph_link_and_an_instant_view_preview(registry, store):
    web = FakeWeb()
    message = await RunUrl(registry, store, f"/url {PAGE}", web)
    assert web.read == [PAGE]
    text = Texts(message)[0]
    assert text == TELEGRAPH  # only the link: the card shows title and preview, repeating them would be noise
    options = message.reply_text.call_args[1]["link_preview_options"]
    assert options.url == TELEGRAPH and not options.is_disabled and not options.prefer_large_media and not options.show_above_text


async def test_notes_about_teasers_and_archives_are_shown(registry, store):
    message = await RunUrl(registry, store, f"/url {PAGE}", FakeWeb(Result(notes=["Only a teaser was available.", "Second note."])))
    text = Texts(message)[0]
    assert text == f"Only a teaser was available.\nSecond note.\n\n{TELEGRAPH}"
    assert message.reply_text.call_args[1]["link_preview_options"].url == TELEGRAPH  # the card is still the article's


async def test_fallback_to_plain_text_when_telegraph_is_unavailable(registry, store):
    message = await RunUrl(registry, store, f"/url {PAGE}", FakeWeb(Result(telegraph=None, text="Paragraph one.\n\nParagraph two.")))
    texts = Texts(message)
    assert texts[0].startswith("The Story") and "Paragraph one." in texts[1] and texts[-1] == f"Source: {PAGE}"
    assert all(call[1]["link_preview_options"].is_disabled for call in message.reply_text.call_args_list)


async def test_long_fallback_text_is_cut_after_three_messages(registry, store):
    message = await RunUrl(registry, store, f"/url {PAGE}", FakeWeb(Result(telegraph=None, text="word " * 5000)))
    texts = Texts(message)
    assert len(texts) == 1 + bot.FALLBACK_CHUNKS + 1 and texts[-1].startswith("…") and all(len(t) <= 4096 for t in texts)


async def test_several_urls_get_one_reply_each_with_tracking_removed(registry, store):
    web = FakeWeb()
    message = await RunUrl(registry, store, f"/url {PAGE}?utm_source=tw https://other.example.org/a#frag", web)
    assert web.read == [PAGE, "https://other.example.org/a"] and message.reply_text.await_count == 2


async def test_hidden_link_in_the_command(registry, store):
    web = FakeWeb()
    entity = MessageEntity(MessageEntity.TEXT_LINK, 5, 4, url=PAGE)
    await RunUrl(registry, store, "/url this", web, entities=(entity,))
    assert web.read == [PAGE]


@pytest.mark.parametrize("text", ["/url", "/url hello", "/url ftp://example.com/file"])
async def test_usage_without_a_web_link(registry, store, text):
    web = FakeWeb()
    message = await RunUrl(registry, store, text, web)
    message.reply_text.assert_awaited_once_with(URL_USAGE)
    assert web.read == []


async def test_social_links_are_pointed_to_social(registry, store):
    web = FakeWeb()
    message = await RunUrl(registry, store, f"/url {TWEET}", web)
    assert "/social" in Texts(message)[0] and web.read == []


async def test_unknown_role_is_denied_without_any_fetch(registry, store):
    web = FakeWeb()
    message = await RunUrl(registry, store, f"/url {PAGE}", web, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert web.read == []


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_user_and_admin_may_use_it_in_groups_too(registry, store, role):
    store.Add(UserRef(telegramId=88), role=role)
    web = FakeWeb()
    await RunUrl(registry, store, f"/url@{BOT} {PAGE}", web, userId=88, chatType=ChatType.SUPERGROUP)
    assert web.read == [PAGE]


async def test_expected_failures_are_reported_with_the_url_and_do_not_notify(registry, store):
    notifier = FakeNotifier()
    message = await RunUrl(registry, store, f"/url {PAGE}", FakeWeb(error=WebError("The site answered with an error (HTTP 404).")), notifier=notifier)
    assert Texts(message)[0] == f"The site answered with an error (HTTP 404).\n{PAGE}"
    notifier.ReportFailure.assert_not_awaited()


async def test_blocked_addresses_get_a_clear_refusal(registry, store):
    message = await RunUrl(registry, store, "/url http://192.168.0.1/admin", FakeWeb(error=UnsafeUrlError("I can't open addresses on private or local networks.")))
    assert "private or local networks" in Texts(message)[0]


async def test_unexpected_failures_notify_the_owner(registry, store):
    cause = ValueError("bug")
    error = UnexpectedWebError("Sorry, something went wrong")
    error.__cause__ = cause
    notifier = FakeNotifier()
    await RunUrl(registry, store, f"/url {PAGE}", FakeWeb(error=error), notifier=notifier)
    assert notifier.ReportFailure.call_args[0][1] is cause


async def test_send_failures_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    message = FakeMessage(f"/url {PAGE}")
    message.reply_text = AsyncMock(side_effect=[RuntimeError("telegram down"), None])
    await MakeUrlCommandCallback(registry, store, FakeWeb(), notifier)(FakeUpdate(message), FakeContext([]))
    assert notifier.ReportFailure.call_args[0][1].args == ("telegram down",)


async def test_not_configured(registry, store):
    message = await RunUrl(registry, store, f"/url {PAGE}", None)
    assert "not configured" in Texts(message)[0]


def test_url_is_registered_for_groups_and_listed(registry, store):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService(), webService=FakeWeb())
    assert "url" in {name for h in app.handlers[0] if isinstance(h, CommandHandler) for name in h.commands}
    assert "/url <link>" in registry.Dispatch("help", [], OWNER_ID, None, store)


# ---- implicit handling of ordinary links -----------------------------------------------------------------

async def Implicit(store, text, web, userId=OWNER_ID, chatType=ChatType.PRIVATE, service=None, **kwargs):
    message = FakeMessage(text, **kwargs)
    await MakeMessageCallback(store, service or FakeService(), None, web)(FakeUpdate(message, userId, chatType=chatType), FakeContext())
    return message


async def test_a_bare_web_link_in_a_private_chat_is_read(store):
    web = FakeWeb()
    message = await Implicit(store, f"have a look {PAGE}?utm_medium=x", web)
    assert web.read == [PAGE] and Texts(message) == [TELEGRAPH]


async def test_social_and_web_links_in_one_message_both_work(store, tmp_path):
    web, service = FakeWeb(), FakeService(MakePost(tmp_path))
    message = await Implicit(store, f"{TWEET} and {PAGE}", web, service=service)
    assert service.loaded == [TWEET] and web.read == [PAGE]
    message.reply_photo.assert_awaited_once()


async def test_group_needs_a_mention(store):
    web = FakeWeb()
    await Implicit(store, f"just chatting {PAGE}", web, chatType=ChatType.GROUP)
    assert web.read == []
    await Implicit(store, f"@{BOT} {PAGE}", web, chatType=ChatType.GROUP)
    assert web.read == [PAGE]


async def test_reply_that_only_mentions_the_bot_reads_the_replied_message_link(store):
    web = FakeWeb()
    original = FakeMessage(f"interesting article {PAGE}")
    await Implicit(store, f"@{BOT}", web, chatType=ChatType.GROUP, replyTo=original)
    assert web.read == [PAGE]


async def test_hidden_link_in_the_replied_message(store):
    web = FakeWeb()
    original = FakeMessage("read this", entities=(MessageEntity(MessageEntity.TEXT_LINK, 0, 4, url=PAGE),))
    await Implicit(store, f"@{BOT}", web, chatType=ChatType.GROUP, replyTo=original)
    assert web.read == [PAGE]


async def test_strangers_get_the_owner_message_and_nothing_is_fetched(store):
    web = FakeWeb()
    message = await Implicit(store, PAGE, web, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert web.read == []


async def test_text_without_any_link_is_unchanged(store):
    web = FakeWeb()
    message = await Implicit(store, "hello there", web)
    assert "Unknown command" in Texts(message)[0] and web.read == []
    mention = await Implicit(store, f"@{BOT} hi", web, chatType=ChatType.GROUP)
    assert "supported link" in Texts(mention)[0]


async def test_web_links_without_a_web_service_say_so(store):
    message = await Implicit(store, PAGE, None)
    assert "not configured" in Texts(message)[0]
