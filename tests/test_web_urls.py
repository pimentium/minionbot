import pytest

from minionbot.web.urls import CleanWebUrl, FindWebUrls


@pytest.mark.parametrize(
    "dirty,clean",
    [
        ("https://example.com/article?utm_source=tw&utm_medium=x&id=7", "https://example.com/article?id=7"),
        ("https://example.com/a?fbclid=IwAR1&gclid=2&keep=1#section", "https://example.com/a?keep=1"),
        ("HTTPS://News.Example.com", "https://News.Example.com/"),
        ("http://example.com/a/b/?MC_EID=1&page=2", "http://example.com/a/b/?page=2"),
        ("https://example.com/path/to/article.html.", "https://example.com/path/to/article.html"),
        ("https://example.com/a?q=a%20b&utm_campaign=z", "https://example.com/a?q=a+b"),
    ],
)
def test_clean_web_url(dirty, clean):
    assert CleanWebUrl(dirty) == clean


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/someuser/",  # social hosts belong to /social, even non-post pages
        "https://x.com/home",
        "https://www.facebook.com/someone",
        "https://www.threads.net/@zuck",
        "https://fb.watch/abc/",
        "https://t.me/somechannel/12",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "not a url",
        "https://",
    ],
)
def test_not_a_web_url(url):
    assert CleanWebUrl(url) is None


def test_find_web_urls_skips_social_links_dedupes_and_keeps_order():
    text = (
        "read https://example.com/a?utm_source=x and https://x.com/jack/status/20 then "
        "https://news.example.org/story, again https://example.com/a and https://www.instagram.com/p/AAA/"
    )
    assert FindWebUrls(text) == ["https://example.com/a", "https://news.example.org/story"]


def test_hidden_text_links_are_included():
    assert FindWebUrls("click here", ["https://example.com/hidden?utm_source=t"]) == ["https://example.com/hidden"]


def test_no_urls():
    assert FindWebUrls("hello there") == [] and FindWebUrls("") == []
