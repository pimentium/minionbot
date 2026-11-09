import httpx
import pytest

from minionbot.social.fetch import MAX_PHOTO_BYTES, MediaFetcher
from minionbot.social.models import PHOTO, VIDEO, DownloadError, Link, RemoteMedia, RemotePost
from minionbot.social.service import SocialService


def Fetcher(handler):
    return MediaFetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def Remote(*media, notes=None):
    return RemotePost("twitter", "u", "@a", "text", list(media), notes or [])


async def test_downloads_files(tmp_path):
    fetcher = Fetcher(lambda request: httpx.Response(200, content=b"abc"))
    post = await fetcher.Download(Remote(RemoteMedia("https://cdn/a.jpg", PHOTO), RemoteMedia("https://cdn/b.mp4", VIDEO)), tmp_path)
    assert [m.kind for m in post.media] == [PHOTO, VIDEO]
    assert all(m.path.read_bytes() == b"abc" for m in post.media)


async def test_oversize_by_header_is_skipped_with_note(tmp_path):
    fetcher = Fetcher(lambda request: httpx.Response(200, headers={"content-length": str(MAX_PHOTO_BYTES + 1)}, content=b"x"))
    post = await fetcher.Download(Remote(RemoteMedia("https://cdn/a.jpg", PHOTO)), tmp_path)
    assert post.media == [] and "https://cdn/a.jpg" in post.notes[0] and list(tmp_path.iterdir()) == []


async def test_oversize_while_streaming_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("minionbot.social.fetch.MAX_PHOTO_BYTES", 5)
    fetcher = Fetcher(lambda request: httpx.Response(200, content=b"0123456789"))
    post = await fetcher.Download(Remote(RemoteMedia("https://cdn/a.jpg", PHOTO)), tmp_path)
    assert post.media == [] and post.notes and list(tmp_path.iterdir()) == []


async def test_http_error_adds_note_and_continues(tmp_path):
    def Handler(request):
        return httpx.Response(404) if "bad" in str(request.url) else httpx.Response(200, content=b"ok")

    post = await Fetcher(Handler).Download(Remote(RemoteMedia("https://cdn/bad.jpg", PHOTO), RemoteMedia("https://cdn/good.jpg", PHOTO)), tmp_path)
    assert len(post.media) == 1 and "Could not download" in post.notes[0]


class StubDownloader:
    def __init__(self, remote=None, error=None):
        self.remote, self.error = remote, error

    def Fetch(self, url):
        if self.error:
            raise self.error
        return self.remote


async def test_service_happy_path(tmp_path):
    fetcher = Fetcher(lambda request: httpx.Response(200, content=b"abc"))
    service = SocialService({"twitter": StubDownloader(Remote(RemoteMedia("https://cdn/a.jpg", PHOTO)))}, fetcher)
    post = await service.Load(Link("twitter", "u"), tmp_path)
    assert post.author == "@a" and len(post.media) == 1


async def test_service_unconfigured_network_and_errors(tmp_path):
    fetcher = Fetcher(lambda request: httpx.Response(200))
    service = SocialService({"twitter": StubDownloader(error=DownloadError("nope")), "threads": StubDownloader(error=ValueError("bug"))}, fetcher)
    with pytest.raises(DownloadError, match="not configured"):
        await service.Load(Link("facebook", "u"), tmp_path)
    with pytest.raises(DownloadError, match="nope"):
        await service.Load(Link("twitter", "u"), tmp_path)
    with pytest.raises(DownloadError, match="something went wrong"):
        await service.Load(Link("threads", "u"), tmp_path)


async def test_video_dimensions_are_read(tmp_path):
    from pathlib import Path

    data = (Path(__file__).parent / "data" / "landscape.mp4").read_bytes()
    fetcher = Fetcher(lambda request: httpx.Response(200, content=data))
    post = await fetcher.Download(Remote(RemoteMedia("https://cdn/v.mp4", VIDEO), RemoteMedia("https://cdn/p.jpg", PHOTO)), tmp_path)
    video, photo = post.media
    assert (video.width, video.height, video.duration) == (640, 360, 3)
    assert photo.width is None


async def test_unreadable_video_still_sent_without_dimensions(tmp_path):
    fetcher = Fetcher(lambda request: httpx.Response(200, content=b"not really an mp4"))
    post = await fetcher.Download(Remote(RemoteMedia("https://cdn/v.mp4", VIDEO)), tmp_path)
    assert post.media[0].width is None
