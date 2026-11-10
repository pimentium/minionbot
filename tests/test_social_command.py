"""Stage 1: the explicit /social command, same pipeline as the implicit link handling."""

from unittest.mock import MagicMock

import pytest
from telegram import MessageEntity
from telegram.constants import ChatType
from telegram.ext import CommandHandler

from minionbot.bot import BuildApplication, MakeMessageCallback, MakeSocialCommandCallback
from minionbot.builtin import SOCIAL_USAGE
from minionbot.config import Config
from minionbot.social.models import PHOTO, DownloadError, UnexpectedDownloadError
from minionbot.users import Role, UserRef

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeService, FakeUpdate, FakeNotifier, MakePost

TWEET = "https://x.com/alice/status/1"
INSTA = "https://www.instagram.com/p/AAA/"


def Callback(registry, store, service, notifier=None):
    return MakeSocialCommandCallback(registry, store, service, notifier)


async def Run(registry, store, text, service, userId=OWNER_ID, chatType=ChatType.PRIVATE, **kwargs):
    message = FakeMessage(text, **kwargs)
    await Callback(registry, store, service)(FakeUpdate(message, userId, chatType=chatType), FakeContext([]))
    return message


async def test_downloads_the_given_link_like_the_implicit_handler(registry, store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = await Run(registry, store, f"/social {TWEET}", service)
    assert service.loaded == [TWEET]
    assert message.reply_photo.call_args[1]["caption"] == "Twitter/X · @alice\n\nhello"


async def test_explicit_and_implicit_paths_give_the_same_reply(registry, store, tmp_path):
    explicit, implicit = FakeService(MakePost(tmp_path)), FakeService(MakePost(tmp_path))
    first = await Run(registry, store, f"/social {TWEET}", explicit)
    second = FakeMessage(TWEET)
    await MakeMessageCallback(store, implicit)(FakeUpdate(second), FakeContext())
    assert explicit.loaded == implicit.loaded
    assert first.reply_photo.call_args[1] == second.reply_photo.call_args[1]


async def test_several_links_one_reply_each_in_order_with_tracking_removed(registry, store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = await Run(registry, store, f"/social {TWEET}?s=46&t=x {INSTA}?igsh=abc", service)
    assert service.loaded == [TWEET, INSTA] and message.reply_photo.await_count == 2


async def test_hidden_text_link_in_the_command_message(registry, store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    entity = MessageEntity(MessageEntity.TEXT_LINK, 8, 4, url=INSTA)
    await Run(registry, store, "/social this", service, entities=(entity,))
    assert service.loaded == [INSTA]


@pytest.mark.parametrize("text", ["/social", "/social hello there", "/social https://example.com/article", "/social https://www.instagram.com/someuser/"])
async def test_usage_when_there_is_no_supported_link(registry, store, text):
    service = FakeService()
    message = await Run(registry, store, text, service)
    message.reply_text.assert_awaited_once_with(SOCIAL_USAGE)
    assert service.loaded == []


@pytest.mark.parametrize("userId", [STRANGER_ID])
async def test_unknown_role_is_denied_and_nothing_is_downloaded(registry, store, userId):
    service = FakeService()
    message = await Run(registry, store, f"/social {TWEET}", service, userId=userId)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert service.loaded == []


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_user_and_admin_may_use_it(registry, store, tmp_path, role):
    store.Add(UserRef(telegramId=77), role=role)
    service = FakeService(MakePost(tmp_path))
    await Run(registry, store, f"/social {TWEET}", service, userId=77)
    assert service.loaded == [TWEET]


async def test_works_in_group_chats_too(registry, store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = await Run(registry, store, f"/social@minionbot {TWEET}", service, chatType=ChatType.SUPERGROUP)
    assert service.loaded == [TWEET] and message.reply_photo.await_count == 1


async def test_download_errors_are_reported_with_the_link(registry, store):
    message = await Run(registry, store, f"/social {TWEET}", FakeService(error=DownloadError("private post")))
    text = message.reply_text.call_args[0][0]
    assert "private post" in text and TWEET in text


async def test_unexpected_failures_notify_the_owner(registry, store):
    cause = ValueError("bug")
    error = UnexpectedDownloadError("Sorry")
    error.__cause__ = cause
    notifier = FakeNotifier()
    message = FakeMessage(f"/social {TWEET}")
    await Callback(registry, store, FakeService(error=error), notifier)(FakeUpdate(message), FakeContext([]))
    assert notifier.ReportFailure.call_args[0][1] is cause


async def test_updates_without_a_message_are_ignored(registry, store):
    update = MagicMock()
    update.effective_message = None
    await Callback(registry, store, FakeService())(update, FakeContext([]))


def test_social_is_registered_for_private_and_group_chats_and_in_help(registry, store):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService())
    handlers = {name: h for h in app.handlers[0] if isinstance(h, CommandHandler) for name in h.commands}
    assert "social" in handlers and "users" in handlers
    # /social accepts group chats, /users does not
    assert repr(handlers["users"].filters) != repr(handlers["social"].filters)
    assert "/social <link>" in registry.Dispatch("help", [], OWNER_ID, None, store)
