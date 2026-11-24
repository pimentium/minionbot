from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from minionbot.social.factory import BuildDownloaders
from minionbot.social.instagram import InstagramDownloader
from minionbot.social.models import PHOTO, VIDEO, DownloadError, SocialAccount

USER = SimpleNamespace(username="alice")
SESSION = "123456789%3Aabcdefghijklmnopqrstuvwxyz"


def FakeClient(info=None, loginError=None, infoError=None):
    client = MagicMock()
    client.login_by_sessionid.side_effect = loginError
    client.media_pk_from_url.return_value = "42"
    if infoError:
        client.media_info.side_effect = infoError
    else:
        client.media_info.return_value = info
    return client


def Media(mediaType, **kwargs):
    base = dict(media_type=mediaType, caption_text="cap", user=USER, thumbnail_url=None, video_url=None, resources=[])
    base.update(kwargs)
    return SimpleNamespace(**base)


def Downloader(client):
    return InstagramDownloader(SESSION, lambda: client)


def test_photo():
    post = Downloader(FakeClient(Media(1, thumbnail_url="https://cdn/p.jpg"))).Fetch("https://instagram.com/p/A/")
    assert (post.author, post.text) == ("@alice", "cap")
    assert [(m.url, m.kind) for m in post.media] == [("https://cdn/p.jpg", PHOTO)]


def test_reel_video():
    post = Downloader(FakeClient(Media(2, video_url="https://cdn/v.mp4", thumbnail_url="https://cdn/t.jpg"))).Fetch("https://instagram.com/reel/A/")
    assert [(m.url, m.kind) for m in post.media] == [("https://cdn/v.mp4", VIDEO)]


def test_album_mixed():
    resources = [Media(1, thumbnail_url="https://cdn/1.jpg"), Media(2, video_url="https://cdn/2.mp4"), Media(1, thumbnail_url="https://cdn/3.jpg")]
    post = Downloader(FakeClient(Media(8, resources=resources))).Fetch("https://instagram.com/p/A/")
    assert [m.kind for m in post.media] == [PHOTO, VIDEO, PHOTO]


def test_logs_in_once_with_session_id():
    client = FakeClient(Media(1, thumbnail_url="https://cdn/p.jpg"))
    downloader = Downloader(client)
    downloader.Fetch("https://instagram.com/p/A/")
    downloader.Fetch("https://instagram.com/p/B/")
    client.login_by_sessionid.assert_called_once_with(SESSION)


def test_eager_login():
    client = FakeClient()
    Downloader(client).Login()
    client.login_by_sessionid.assert_called_once_with(SESSION)


def test_login_failure_hides_session_id(caplog):
    client = FakeClient(loginError=RuntimeError(f"rejected {SESSION}"))
    with pytest.raises(DownloadError, match="expired") as error:
        Downloader(client).Fetch("https://instagram.com/p/A/")
    assert SESSION not in str(error.value) and SESSION not in caplog.text


def test_fetch_failure():
    with pytest.raises(DownloadError):
        Downloader(FakeClient(infoError=RuntimeError("404"))).Fetch("https://instagram.com/p/A/")


def test_factory_uses_instagrapi_only_with_session_id():
    assert isinstance(BuildDownloaders({"instagram": SocialAccount(sessionId=SESSION)})["instagram"], InstagramDownloader)
    assert not isinstance(BuildDownloaders({})["instagram"], InstagramDownloader)


STORY = "https://www.instagram.com/stories/alice/4037489269225376125/"


def StoryClient(info=None, error=None):
    client = MagicMock()
    client.story_pk_from_url.return_value = "4037489269225376125"
    if error:
        client.story_info.side_effect = error
    else:
        client.story_info.return_value = info
    return client


def Story(mediaType, **kwargs):  # instagrapi's Story has no caption_text
    base = dict(media_type=mediaType, user=USER, thumbnail_url=None, video_url=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_story_photo_uses_story_info_not_media_info():
    client = StoryClient(Story(1, thumbnail_url="https://cdn/s.jpg"))
    post = Downloader(client).Fetch(STORY)
    client.story_info.assert_called_once_with("4037489269225376125")
    client.media_info.assert_not_called()
    assert (post.author, post.text) == ("@alice", "") and [(m.url, m.kind) for m in post.media] == [("https://cdn/s.jpg", PHOTO)]


def test_story_video():
    post = Downloader(StoryClient(Story(2, video_url="https://cdn/s.mp4", thumbnail_url="https://cdn/t.jpg"))).Fetch(STORY)
    assert [(m.url, m.kind) for m in post.media] == [("https://cdn/s.mp4", VIDEO)]


def test_an_expired_story_has_its_own_message():
    with pytest.raises(DownloadError, match="story.*24 hours"):
        Downloader(StoryClient(error=RuntimeError("gone"))).Fetch(STORY)
