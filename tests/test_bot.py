from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import MessageEntity
from telegram.constants import ChatType
from telegram.ext import CommandHandler, MessageHandler

from minionbot import bot
from minionbot.bot import BuildApplication, MakeCommandCallback, MakeMessageCallback
from minionbot.config import Config
from minionbot.social.models import PHOTO, VIDEO, DownloadError, LocalMedia, Post
from minionbot.users import UserRef

from tests.conftest import OWNER_ID, STRANGER_ID

BOT = "minionbot"
TWEET = "https://x.com/alice/status/1"


def FakeMessage(text, replyTo=None, entities=()):
    message = MagicMock()
    message.text, message.caption = text, None
    message.entities, message.caption_entities = entities, ()
    message.reply_to_message = replyTo
    message.quote = message.external_reply = message.forward_origin = None
    for method in ("reply_text", "reply_photo", "reply_video", "reply_media_group"):
        setattr(message, method, AsyncMock())
    return message


def FakeUpdate(message, userId=OWNER_ID, username=None, chatType=ChatType.PRIVATE):
    update = MagicMock()
    update.effective_message = message
    update.effective_user.id = userId
    update.effective_user.username = username
    update.effective_chat.type = chatType
    update.effective_chat.send_action = AsyncMock()
    return update


def FakeContext(args=None):
    context = MagicMock()
    context.args = args
    context.bot.username = BOT
    context.application.bot_data = {}
    return context


class FakeService:
    def __init__(self, post=None, error=None):
        self.post, self.error, self.loaded = post, error, []

    async def Load(self, link, directory):
        self.loaded.append(link.url)
        if self.error:
            raise self.error
        return self.post


def MakePost(tmp_path, kinds=(PHOTO,), text="hello"):
    media = []
    for index, kind in enumerate(kinds):
        path = tmp_path / f"{index}.bin"
        path.write_bytes(b"data")
        media.append(LocalMedia(path, kind))
    return Post("twitter", TWEET, "@alice", text, media)


def test_build_application_registers_handlers(store, registry):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService())
    handlers = app.handlers[0]
    commandNames = {n for h in handlers if isinstance(h, CommandHandler) for n in h.commands}
    assert commandNames == set(registry.Names())
    assert isinstance(handlers[-1], MessageHandler)  # catch-all last


async def test_command_callback_replies_and_denies(store, registry):
    update = FakeUpdate(FakeMessage("/users list"))
    await MakeCommandCallback(registry, store, "users")(update, FakeContext(["list"]))
    assert "owner" in update.effective_message.reply_text.call_args[0][0]
    stranger = FakeUpdate(FakeMessage("/users list"), STRANGER_ID)
    await MakeCommandCallback(registry, store, "users")(stranger, FakeContext(["list"]))
    stranger.effective_message.reply_text.assert_awaited_once_with(store.DeniedText())


async def test_stranger_gets_owner_message_for_link_and_nothing_downloaded(store):
    service = FakeService()
    update = FakeUpdate(FakeMessage(TWEET), STRANGER_ID, "mallory")
    await MakeMessageCallback(store, service)(update, FakeContext())
    update.effective_message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert "Please ask my owner" in store.DeniedText() and service.loaded == []


async def test_stranger_mention_in_group_denied_but_plain_group_chat_ignored(store):
    service = FakeService()
    mention = FakeUpdate(FakeMessage(f"@{BOT} {TWEET}"), STRANGER_ID, chatType=ChatType.SUPERGROUP)
    await MakeMessageCallback(store, service)(mention, FakeContext())
    mention.effective_message.reply_text.assert_awaited_once_with(store.DeniedText())
    plain = FakeUpdate(FakeMessage(f"chatting {TWEET}"), STRANGER_ID, chatType=ChatType.SUPERGROUP)
    await MakeMessageCallback(store, service)(plain, FakeContext())
    plain.effective_message.reply_text.assert_not_awaited()


async def test_private_link_sends_photo_with_caption(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert service.loaded == [TWEET]
    args, kwargs = message.reply_photo.call_args
    assert kwargs["caption"] == "Twitter/X · @alice\n\nhello"


async def test_one_reply_per_link(store, tmp_path):
    service = FakeService(MakePost(tmp_path, (VIDEO,)))
    message = FakeMessage(f"{TWEET} https://x.com/bob/status/2")
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert len(service.loaded) == 2 and message.reply_video.await_count == 2


async def test_album_split_into_groups_of_ten(store, tmp_path):
    service = FakeService(MakePost(tmp_path, (PHOTO,) * 11))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert message.reply_media_group.await_count == 1 and message.reply_photo.await_count == 1
    assert len(message.reply_media_group.call_args[0][0]) == 10


async def test_long_text_follows_as_messages(store, tmp_path):
    service = FakeService(MakePost(tmp_path, text="word " * 600))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert message.reply_photo.call_args[1]["caption"] == "Twitter/X · @alice"
    assert message.reply_text.await_count >= 1


async def test_text_only_post(store, tmp_path):
    service = FakeService(MakePost(tmp_path, ()))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert "hello" in message.reply_text.call_args[0][0]


async def test_reply_with_only_mention_uses_replied_message(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    original = FakeMessage(f"look {TWEET}")
    reply = FakeMessage(f"@{BOT}", replyTo=original)
    update = FakeUpdate(reply, chatType=ChatType.GROUP)
    await MakeMessageCallback(store, service)(update, FakeContext())
    assert service.loaded == [TWEET]
    reply.reply_photo.assert_awaited_once()


async def test_hidden_text_link_entity(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    entity = MessageEntity(MessageEntity.TEXT_LINK, 0, 4, url="https://www.instagram.com/p/AAA/")
    message = FakeMessage("this", entities=(entity,))
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert service.loaded == ["https://www.instagram.com/p/AAA/"]


async def test_download_error_is_reported_with_link(store):
    service = FakeService(error=DownloadError("Could not read this post."))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    text = message.reply_text.call_args[0][0]
    assert "Could not read" in text and TWEET in text


async def test_unexpected_send_failure_is_contained(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = FakeMessage(TWEET)
    message.reply_photo.side_effect = RuntimeError("telegram down")
    await MakeMessageCallback(store, service)(FakeUpdate(message), FakeContext())
    assert "couldn't send" in message.reply_text.call_args[0][0]


async def test_known_user_without_links(store):
    service = FakeService()
    plain = FakeMessage("hello")
    await MakeMessageCallback(store, service)(FakeUpdate(plain), FakeContext())
    assert "Unknown command" in plain.reply_text.call_args[0][0]
    mention = FakeMessage(f"@{BOT} hi")
    await MakeMessageCallback(store, service)(FakeUpdate(mention, chatType=ChatType.GROUP), FakeContext())
    assert "supported link" in mention.reply_text.call_args[0][0]


async def test_added_user_can_download(store, tmp_path):
    store.Add(UserRef(telegramId=555), name="friend")
    service = FakeService(MakePost(tmp_path))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, service)(FakeUpdate(message, 555), FakeContext())
    message.reply_photo.assert_awaited_once()


async def test_updates_without_message_are_ignored(store):
    update = MagicMock()
    update.effective_message = None
    await MakeMessageCallback(store, FakeService())(update, FakeContext())


async def test_video_is_sent_with_real_dimensions(store, tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"data")
    post = Post("twitter", TWEET, "@alice", "hi", [LocalMedia(path, VIDEO, 1280, 720, 7)])
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, FakeService(post))(FakeUpdate(message), FakeContext())
    kwargs = message.reply_video.call_args[1]
    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (1280, 720, 7)


async def test_video_without_dimensions_omits_them(store, tmp_path):
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, FakeService(MakePost(tmp_path, (VIDEO,))))(FakeUpdate(message), FakeContext())
    assert "width" not in message.reply_video.call_args[1]


async def test_album_videos_carry_dimensions(store, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"data")
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"data")
    post = Post("twitter", TWEET, "@alice", "hi", [LocalMedia(video, VIDEO, 640, 360, 3), LocalMedia(photo, PHOTO)])
    message = FakeMessage(TWEET)
    await MakeMessageCallback(store, FakeService(post))(FakeUpdate(message), FakeContext())
    album = message.reply_media_group.call_args[0][0]
    assert (album[0].width, album[0].height, album[0].duration) == (640, 360, 3)


async def test_log_bot_info_reports_privacy_mode(caplog):
    import logging

    application = MagicMock()
    application.bot.get_me = AsyncMock(return_value=MagicMock(username=BOT, can_read_all_group_messages=False))
    with caplog.at_level(logging.INFO, logger="minionbot.bot"):
        await bot.LogBotInfo(application)
    assert f"@{BOT}" in caplog.text and "privacy mode: ON" in caplog.text
    application.bot.get_me.return_value = MagicMock(username=BOT, can_read_all_group_messages=True)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="minionbot.bot"):
        await bot.LogBotInfo(application)
    assert "privacy mode: OFF" in caplog.text


async def test_received_messages_are_logged_even_when_ignored(store, caplog):
    import logging

    update = FakeUpdate(FakeMessage("just chatting"), chatType=ChatType.GROUP)
    with caplog.at_level(logging.INFO, logger="minionbot.bot"):
        await MakeMessageCallback(store, FakeService())(update, FakeContext())
    assert "ignore" in caplog.text and "group" in caplog.text and "just chatting" not in caplog.text


async def test_reply_with_quote_excerpt_uses_quoted_link(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    reply = FakeMessage(f"@{BOT}", replyTo=FakeMessage("cannot see this"))
    reply.quote = MagicMock(text="https://www.instagram.com/reel/DfRaAr0Nnfc/")
    await MakeMessageCallback(store, service)(FakeUpdate(reply, chatType=ChatType.SUPERGROUP), FakeContext())
    assert service.loaded == ["https://www.instagram.com/reel/DfRaAr0Nnfc/"]


async def test_reply_to_external_message_with_link_preview(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    reply = FakeMessage(f"@{BOT}")
    reply.external_reply = MagicMock(link_preview_options=MagicMock(url="https://x.com/a/status/9"))
    await MakeMessageCallback(store, service)(FakeUpdate(reply, chatType=ChatType.SUPERGROUP), FakeContext())
    assert service.loaded == ["https://x.com/a/status/9"]


async def test_no_links_logs_diagnostics_without_content(store, caplog):
    import logging

    original = FakeMessage("secret words without links")
    reply = FakeMessage(f"@{BOT}", replyTo=original)
    with caplog.at_level(logging.WARNING, logger="minionbot.bot"):
        await MakeMessageCallback(store, FakeService())(FakeUpdate(reply, chatType=ChatType.GROUP), FakeContext())
    assert "reply_to_message: 26 chars" in caplog.text and "secret words" not in caplog.text
    assert "supported link" in reply.reply_text.call_args[0][0]


async def test_privacy_hint_when_reply_content_is_missing(store):
    context = FakeContext()
    context.application.bot_data[bot.PRIVACY_ON_KEY] = True
    message = FakeMessage(f"@{BOT}")
    await MakeMessageCallback(store, FakeService())(FakeUpdate(message, chatType=ChatType.GROUP), context)
    assert "privacy mode" in message.reply_text.call_args[0][0]


async def test_no_privacy_hint_when_privacy_off_or_reply_present(store):
    context = FakeContext()
    context.application.bot_data[bot.PRIVACY_ON_KEY] = False
    message = FakeMessage(f"@{BOT}")
    await MakeMessageCallback(store, FakeService())(FakeUpdate(message, chatType=ChatType.GROUP), context)
    assert "privacy mode" not in message.reply_text.call_args[0][0]
    context.application.bot_data[bot.PRIVACY_ON_KEY] = True
    withReply = FakeMessage(f"@{BOT}", replyTo=FakeMessage("no link here"))
    await MakeMessageCallback(store, FakeService())(FakeUpdate(withReply, chatType=ChatType.GROUP), context)
    assert "privacy mode" not in withReply.reply_text.call_args[0][0]


async def test_log_bot_info_records_privacy_flag():
    application = MagicMock()
    application.bot_data = {}
    application.bot.get_me = AsyncMock(return_value=MagicMock(username=BOT, can_read_all_group_messages=False))
    await bot.LogBotInfo(application)
    assert application.bot_data[bot.PRIVACY_ON_KEY] is True
