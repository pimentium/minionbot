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


# ---- structure observed on a real public page (placeholder texts) --------------------------------

def FullPost(code, text, pk, username="alice", fullName="Alice A", **extra):
    return {
        "code": code, "pk": pk, "id": f"{pk}_1", "media_type": 19,
        "user": {"username": username, "full_name": fullName},
        "caption": {"text": text}, "image_versions2": {"candidates": []}, "video_versions": None, "carousel_media": None,
        **extra,
    }


def Scripts(*items):
    return "<html>" + "".join(f'<script type="application/json" data-sjs>{json.dumps(item)}</script>' for item in items) + "</html>"


def RealLayoutPage(parts, orderedPks=None):
    """The target post is a full object in one script; its later parts hang off a partial copy {id, text_post_app_info}."""
    target = FullPost("CODE0", "part one", "100")
    nodes = [{"node": FullPost(f"CODE{i}", text, str(100 + i))} for i, text in enumerate(parts, start=1)]
    order = orderedPks or [str(100 + i) for i in range(1, len(parts) + 1)]
    partial = {
        "id": target["id"],
        "text_post_app_info": {"self_thread": {"posts": {"edges": nodes}, "post_ids": {"edges": [{"node": {"pk": pk}} for pk in order]}}},
    }
    first = {"data": {"media": target}}
    second = {"data": {"media": partial}}
    return Scripts(first, second)


URL0 = "https://www.threads.com/@alice/post/CODE0"


def test_self_thread_parts_are_appended_in_page_order():
    result = ParseThreadsPage(URL0, RealLayoutPage(["part two", "part three"]))
    assert result.text == "part one\n\npart two\n\npart three"
    assert result.author == "Alice A (@alice)"


def test_post_ids_define_the_order_when_edges_disagree():
    result = ParseThreadsPage(URL0, RealLayoutPage(["second", "third"], orderedPks=["102", "101"]))
    assert result.text == "part one\n\nthird\n\nsecond"


def test_text_only_post_has_no_media_and_no_error():
    result = ParseThreadsPage(URL0, RealLayoutPage([]))
    assert result.media == [] and result.text == "part one"


def test_parts_by_other_authors_are_not_merged():
    page = RealLayoutPage(["mine"])
    stranger = {"id": "100_1", "text_post_app_info": {"self_thread": {"posts": {"edges": [{"node": FullPost("X1", "not mine", "555", username="bob")}]}}}}
    html = page.replace("</html>", "") + f'<script type="application/json" data-sjs>{json.dumps({"x": stranger})}</script></html>'
    result = ParseThreadsPage(URL0, html)
    assert "not mine" not in result.text and "mine" in result.text


def test_media_of_later_parts_is_collected():
    target = FullPost("CODE0", "one", "100")
    part = FullPost("CODE1", "two", "101", image_versions2={"candidates": [{"url": "https://cdn/p.jpg"}]})
    partial = {"id": target["id"], "text_post_app_info": {"self_thread": {"posts": {"edges": [{"node": part}]}}}}
    result = ParseThreadsPage(URL0, Scripts({"data": {"media": target}}, {"data": {"media": partial}}))
    assert [(m.url, m.kind) for m in result.media] == [("https://cdn/p.jpg", PHOTO)]


def test_author_label_without_full_name():
    page = Page({"data": {"media": FullPost("CODE0", "x", "100", fullName="")}})
    assert ParseThreadsPage(URL0, page).author == "@alice"


def test_downloader_sends_browser_headers_that_avoid_zstd():
    import httpx

    from minionbot.social.threads import PAGE_HEADERS, ThreadsDownloader

    seen = {}

    def Handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text=RealLayoutPage(["two"]))

    client = httpx.Client(transport=httpx.MockTransport(Handler), headers=PAGE_HEADERS)
    post = ThreadsDownloader(client).Fetch(URL0)
    assert "Firefox" in seen["user-agent"] and "zstd" not in seen["accept-encoding"] and seen["sec-fetch-mode"] == "navigate"
    assert post.text == "part one\n\ntwo"
    assert "Firefox" in ThreadsDownloader().client.headers["user-agent"]


def test_http_error_becomes_download_error():
    import httpx

    from minionbot.social.threads import ThreadsDownloader

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    with pytest.raises(DownloadError):
        ThreadsDownloader(client).Fetch(URL0)
