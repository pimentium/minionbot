"""Social network links that are not downloadable posts: say so and suggest /url instead of "Unknown command"."""

import pytest
from telegram.constants import ChatType

from minionbot.bot import UNSUPPORTED_TEXT, MakeMessageCallback, MakeSocialCommandCallback, MakeUrlCommandCallback
from minionbot.commands import UNKNOWN_COMMAND_TEXT
from minionbot.social.links import FindUnsupportedSocialUrls, IsUnsupportedSocialUrl
from minionbot.social.triggers import UNSUPPORTED, Decide, Incoming
from minionbot.web.urls import FindWebUrls

from tests.conftest import OWNER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeService, FakeUpdate, MakePost
from tests.test_url_command import FakeWeb, Texts

PROFILE = "https://www.instagram.com/someuser/"
HIGHLIGHT = "https://www.instagram.com/stories/highlights/17912345678901234/"
FEED = "https://x.com/home"
FB_PAGE = "https://www.facebook.com/somepage"
POST = "https://www.instagram.com/p/ABC123/"


@pytest.mark.parametrize("url", [PROFILE, HIGHLIGHT, FEED, FB_PAGE, "https://www.threads.net/@someone", "https://fb.watch/"])
def test_unsupported_social_urls_are_detected(url):
    assert IsUnsupportedSocialUrl(url) and FindUnsupportedSocialUrls(f"see {url}.") == [url]


@pytest.mark.parametrize("url", [POST, "https://x.com/a/status/1", "https://example.com/x", "https://t.me/channel/1"])
def test_supported_posts_and_other_sites_are_not_unsupported_social_urls(url):
    assert not IsUnsupportedSocialUrl(url)


def test_web_urls_skip_social_pages_unless_asked_explicitly():
    assert FindWebUrls(f"{PROFILE} {POST} https://example.com/a") == ["https://example.com/a"]
    assert FindWebUrls(f"{PROFILE} {POST} https://example.com/a", includeSocial=True) == [PROFILE, "https://example.com/a"]


def test_decide_reports_only_unsupported_links_as_such():
    decision = Decide(True, "bot", Incoming(f"look {PROFILE}"))
    assert decision.kind == UNSUPPORTED and decision.unsupported == [PROFILE] and not decision.links and not decision.urls


def test_decide_still_processes_a_supported_link_next_to_an_unsupported_one():
    decision = Decide(True, "bot", Incoming(f"{POST} {PROFILE}"))
    assert decision.kind != UNSUPPORTED and len(decision.links) == 1 and decision.unsupported == [PROFILE]


def test_in_groups_the_unsupported_link_needs_a_mention():
    assert Decide(False, "bot", Incoming(f"{PROFILE}")).kind == "ignore"
    assert Decide(False, "bot", Incoming(f"@bot {PROFILE}")).kind == UNSUPPORTED


async def Send(store, text, service=None, web=None, chatType=ChatType.PRIVATE):
    message = FakeMessage(text)
    await MakeMessageCallback(store, service or FakeService(), None, web)(FakeUpdate(message, OWNER_ID, chatType=chatType), FakeContext())
    return message


async def test_a_bare_unsupported_link_is_explained_not_called_an_unknown_command(store):
    message = await Send(store, PROFILE)
    text = message.reply_text.call_args[0][0]
    assert text == UNSUPPORTED_TEXT.format(urls=PROFILE) and text != UNKNOWN_COMMAND_TEXT
    assert "/url" in text and message.reply_text.await_count == 1
    assert message.reply_text.call_args[1]["link_preview_options"].is_disabled


async def test_nothing_is_downloaded_or_read_for_an_unsupported_link(store):
    service, web = FakeService(), FakeWeb()
    await Send(store, HIGHLIGHT, service, web)
    assert service.loaded == [] and web.read == []


async def test_a_supported_post_is_still_downloaded_and_the_unsupported_one_is_mentioned(store, tmp_path):
    service = FakeService(MakePost(tmp_path))
    message = await Send(store, f"{POST} {PROFILE}", service)
    assert service.loaded == [POST]
    assert UNSUPPORTED_TEXT.format(urls=PROFILE) in [call[0][0] for call in message.reply_text.call_args_list]


async def test_strangers_are_still_denied(store):
    message = FakeMessage(PROFILE)
    await MakeMessageCallback(store, FakeService(), None, None)(FakeUpdate(message, 9999), FakeContext())
    message.reply_text.assert_awaited_once_with(store.DeniedText())


async def test_social_command_explains_an_unsupported_link(registry, store):
    message = FakeMessage(f"/social {PROFILE}")
    await MakeSocialCommandCallback(registry, store, FakeService())(FakeUpdate(message), FakeContext([]))
    assert message.reply_text.call_args[0][0] == UNSUPPORTED_TEXT.format(urls=PROFILE)


async def test_url_command_does_try_to_read_an_unsupported_social_page(registry, store):
    web = FakeWeb()
    message = FakeMessage(f"/url {PROFILE}")
    await MakeUrlCommandCallback(registry, store, web)(FakeUpdate(message), FakeContext([]))
    assert web.read == [PROFILE]


async def test_url_command_still_sends_supported_posts_to_social(registry, store):
    web = FakeWeb()
    message = FakeMessage(f"/url {POST}")
    await MakeUrlCommandCallback(registry, store, web)(FakeUpdate(message), FakeContext([]))
    assert web.read == [] and "/social" in Texts(message)[0]
