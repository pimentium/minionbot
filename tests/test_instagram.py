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
