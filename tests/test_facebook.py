import json
from unittest.mock import MagicMock

import httpx
import pytest

from minionbot.social.facebook import (
    Combine,
    FacebookDownloader,
    FacebookPageReader,
    FacebookStory,
    ParseFacebookPage,
)
from minionbot.social.factory import BuildDownloaders
from minionbot.social.models import PHOTO, DownloadError, RemoteMedia, RemotePost, SocialAccount

URL = "https://www.facebook.com/sharer/posts/pfbidX"


# Structure taken from a real page (story -> attached_story, each with actors/message/post_id); text is placeholder.
def Story(author, text, attached=None, postId="1"):
    story = {"actors": [{"name": author}], "message": {"text": text} if text is not None else None, "post_id": postId, "comet_sections": {}}
    if attached:
        story["attached_story"] = attached
    return story


def Page(story, extraScripts=()):
    data = {"data": {"node_v2": {"comet_sections": {"content": {"story": story}}}}}
    scripts = [f'<script type="application/json" data-sjs>{json.dumps(item)}</script>' for item in [*extraScripts, data]]
    return "<html>" + "".join(scripts) + "</html>"


SHARED_PAGE = Page(Story("Sharer", "My comment", Story("Original", "Original text", postId="2")))


def test_parse_shared_post():
    story = ParseFacebookPage(SHARED_PAGE)
    assert (story.author, story.text) == ("Sharer", "My comment")
    assert (story.shared.author, story.shared.text) == ("Original", "Original text")


def test_parse_plain_post_and_missing_message():
    story = ParseFacebookPage(Page(Story("Poster", None)))
    assert (story.author, story.text, story.shared) == ("Poster", "", None)


def test_parse_ignores_broken_scripts_and_pages_without_stories():
    assert ParseFacebookPage('<script type="application/json">{broken</script>') is None
    assert ParseFacebookPage(Page({"unrelated": 1})) is None
    assert ParseFacebookPage("<html>login wall</html>") is None


def test_parse_takes_first_story_in_document_order():
    other = {"data": {"story": Story("Feed Item", "unrelated", postId="9")}}
    # the page's own story comes first in real pages; later feed items must not win
    html = Page(Story("Sharer", "mine")) + Page(other["data"]["story"])
    assert ParseFacebookPage(html).author == "Sharer"


def GalleryPost(author="Original", text="Original text"):
    return RemotePost("facebook", URL, author, text, [RemoteMedia("https://cdn/1.jpg", PHOTO)])


def test_combine_shared_post_shows_sharer_then_original():
    post = Combine(GalleryPost(), FacebookStory("Sharer", "My comment", FacebookStory("Original", "Original text")))
    assert post.author == "Sharer"
    assert post.text == "My comment\n\n↪ Shared post by Original:\nOriginal text"
    assert len(post.media) == 1


def test_combine_shared_without_comment_and_with_gallery_text_only():
    post = Combine(GalleryPost(), FacebookStory("Sharer", "", FacebookStory("", "")))
    assert post.author == "Sharer" and post.text == "↪ Shared post by Original:\nOriginal text"


def test_combine_plain_post_prefers_page_then_gallery():
    assert Combine(GalleryPost("G", "gtext"), FacebookStory("Page", "ptext")).text == "ptext"
    kept = Combine(GalleryPost("G", "gtext"), FacebookStory("", ""))
    assert (kept.author, kept.text) == ("G", "gtext")
    assert Combine(GalleryPost("G", "gtext"), None).author == "G"


class StubGallery:
    def __init__(self, post=None, error=None):
        self.post, self.error = post, error

    def Fetch(self, url):
        if self.error:
            raise self.error
        return self.post


class StubReader:
    def __init__(self, story):
        self.story = story

    def Read(self, url):
        return self.story


def test_downloader_combines_both_sources():
    reader = StubReader(FacebookStory("Sharer", "My comment", FacebookStory("Original", "Original text")))
    post = FacebookDownloader(StubGallery(GalleryPost()), reader).Fetch(URL)
    assert post.author == "Sharer" and "My comment" in post.text and post.media


def test_downloader_text_only_post_when_gallery_finds_no_media():
    reader = StubReader(FacebookStory("Poster", "Just words"))
    post = FacebookDownloader(StubGallery(error=DownloadError("Nothing found")), reader).Fetch(URL)
    assert (post.author, post.text, post.media) == ("Poster", "Just words", [])


def test_downloader_raises_when_both_sources_fail():
    with pytest.raises(DownloadError):
        FacebookDownloader(StubGallery(error=DownloadError("Nothing found")), StubReader(None)).Fetch(URL)
    with pytest.raises(DownloadError):
        FacebookDownloader(StubGallery(error=DownloadError("x")), StubReader(FacebookStory("A", ""))).Fetch(URL)


def test_downloader_works_when_page_cannot_be_read():
    post = FacebookDownloader(StubGallery(GalleryPost()), StubReader(None)).Fetch(URL)
    assert post.author == "Original"


def test_reader_fetches_with_browser_headers_and_parses():
    seen = {}

    def Handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text=SHARED_PAGE)

    client = httpx.Client(transport=httpx.MockTransport(Handler), headers={"User-Agent": "x"})
    assert FacebookPageReader(client=client).Read(URL).author == "Sharer"


def test_reader_default_headers_avoid_zstd_and_use_browser_agent():
    from minionbot.social.facebook import PAGE_HEADERS

    assert "zstd" not in PAGE_HEADERS["Accept-Encoding"] and "Firefox" in PAGE_HEADERS["User-Agent"]


def test_reader_returns_none_on_http_errors():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, text="Error")))
    assert FacebookPageReader(client=client).Read(URL) is None


def test_reader_survives_unreadable_cookie_file(tmp_path):
    assert FacebookPageReader(str(tmp_path / "missing.txt")).client is not None


def test_factory_builds_facebook_downloader():
    assert isinstance(BuildDownloaders({"facebook": SocialAccount(cookiesFile="/nonexistent")})["facebook"], FacebookDownloader)
    assert isinstance(BuildDownloaders({})["facebook"], FacebookDownloader)
