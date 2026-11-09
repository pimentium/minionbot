import json

import pytest

from minionbot.social.models import PHOTO, VIDEO, DownloadError
from minionbot.social.threads import ParseThreadsPage

URL = "https://www.threads.net/@alice/post/CODE2"


def Post(code, username, text, **extra):
    return {"code": code, "user": {"username": username}, "caption": {"text": text} if text else None, **extra}


def Page(data):
    return f'<html><script type="application/json" data-sjs>{json.dumps(data)}</script></html>'


# NOTE: fixtures follow the page structure as understood from public examples; not verified against live Threads.
def test_thread_parts_of_author_are_combined():
    items = [
        {"post": Post("CODE1", "alice", "first", image_versions2={"candidates": [{"url": "https://cdn/1.jpg"}]})},
        {"post": Post("CODE2", "alice", "second", video_versions=[{"url": "https://cdn/2.mp4"}])},
        {"post": Post("CODE9", "bob", "reply by bob")},
    ]
    html = Page({"require": [{"x": {"thread_items": items}}]})
    post = ParseThreadsPage(URL, html)
    assert post.author == "@alice" and post.text == "first\n\nsecond"
    assert [m.kind for m in post.media] == [PHOTO, VIDEO]


def test_carousel_post_without_thread_items():
    post = Post("CODE2", "alice", "album", carousel_media=[{"image_versions2": {"candidates": [{"url": "https://cdn/a.jpg"}]}}, {"video_versions": [{"url": "https://cdn/b.mp4"}]}])
    result = ParseThreadsPage(URL, Page({"deep": {"nest": [post]}}))
    assert [m.kind for m in result.media] == [PHOTO, VIDEO]


def test_not_found_raises():
    with pytest.raises(DownloadError):
        ParseThreadsPage(URL, "<html>login wall</html>")
