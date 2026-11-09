import pytest

from minionbot.social.links import Classify, FindLinks
from minionbot.social.models import Link


@pytest.mark.parametrize(
    "url,network",
    [
        ("https://www.instagram.com/p/ABC123/?igsh=xyz", "instagram"),
        ("https://instagram.com/reel/ABC_123/", "instagram"),
        ("https://www.instagram.com/someuser/reel/ABC123/", "instagram"),
        ("https://twitter.com/jack/status/20", "twitter"),
        ("https://x.com/jack/status/20?s=46&t=abc", "twitter"),
        ("https://mobile.twitter.com/i/web/status/20", "twitter"),
        ("https://www.threads.net/@zuck/post/C1abcDEF", "threads"),
        ("https://www.threads.com/@zuck/post/C1abcDEF?xmt=1", "threads"),
        ("https://www.facebook.com/someone/posts/12345", "facebook"),
        ("https://www.facebook.com/photo.php?fbid=1", "facebook"),
        ("https://www.facebook.com/watch/?v=123", "facebook"),
        ("https://www.facebook.com/reel/123", "facebook"),
        ("https://www.facebook.com/share/p/AbC/", "facebook"),
        ("https://fb.watch/abcDEF/", "facebook"),
    ],
)
def test_classify_supported(url, network):
    assert Classify(url).network == network


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/someuser/",
        "https://twitter.com/jack",
        "https://x.com/home",
        "https://www.threads.net/@zuck",
        "https://www.facebook.com/someone",
        "https://example.com/p/ABC",
        "https://evil.com/?u=https://x.com/jack/status/20",
        "https://notinstagram.com/p/ABC",
    ],
)
def test_classify_rejects(url):
    assert Classify(url) is None


def test_tracking_params_are_stripped_but_facebook_query_kept():
    assert Classify("https://x.com/jack/status/20?s=46").url == "https://x.com/jack/status/20"
    assert Classify("https://www.facebook.com/watch/?v=123&x=1").url == "https://www.facebook.com/watch/?v=123&x=1"


def test_find_links_order_dedupe_and_punctuation():
    text = "look https://x.com/a/status/1, and (https://www.instagram.com/p/XYZ/). again https://x.com/a/status/1"
    assert FindLinks(text) == [Link("twitter", "https://x.com/a/status/1"), Link("instagram", "https://www.instagram.com/p/XYZ/")]


def test_find_links_includes_hidden_text_links():
    links = FindLinks("click here", ["https://www.threads.net/@a/post/Cx1"])
    assert links == [Link("threads", "https://www.threads.net/@a/post/Cx1")]


def test_find_links_none():
    assert FindLinks("hello https://example.com") == []
