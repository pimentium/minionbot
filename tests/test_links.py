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


FB_POST = "https://www.facebook.com/andrey.sverdlichenko/posts/pfbid02x1tqPGNTW8ykd7w2nq56bVZwUFSWjsEvrDWcZd6v2Z59vN4CGNqA58GFAbCXobAWl"
FB_TAIL = (
    "?__cft__[0]=AZj7QLMqutknwSTq1bnEWCBXYS-b8n1oinOv5sF-oWiW8EsJtoCsljs62iiZjaOA6JCi7LZWl89qAX1kXySh-CkhW63RjvOT7nq0hSyV3Rnp8CLx"
    "GN-FfdToEsJkSo1oNPi0znyqBGf9q0tBoduSGpWwXLMoXrusgxG0rM-NcwOL1xKfpVg9iXNS-F0xdXj3qFIrN4wwnQthToP99exAbww&__tn__=%2CO%2CP-R"
)


@pytest.mark.parametrize(
    "dirty,clean",
    [
        # the three examples from the owner
        ("https://www.instagram.com/reel/DfPhSdfNDFc/?rpxt=MXVnYm9pZ2FmdjQzMw==", "https://www.instagram.com/reel/DfPhSdfNDFc/"),
        ("https://www.instagram.com/p/DeFGPzDjPab/?stkn=ZjFieXR2ZDh5MDR2", "https://www.instagram.com/p/DeFGPzDjPab/"),
        (FB_POST + FB_TAIL, FB_POST),
        # more Instagram / Twitter / Threads
        ("https://instagram.com/p/ABC123?igsh=xyz&utm_source=qr#frag", "https://www.instagram.com/p/ABC123/"),
        ("https://m.instagram.com/someuser/reel/ABC_1/extra/segments?x=1", "https://www.instagram.com/someuser/reel/ABC_1/"),
        ("https://x.com/jack/status/20?s=46&t=abc&ref_src=twsrc", "https://x.com/jack/status/20"),
        ("https://mobile.twitter.com/jack/status/20/?s=1", "https://twitter.com/jack/status/20"),
        ("https://www.twitter.com/i/web/status/20?utm_medium=x", "https://twitter.com/i/web/status/20"),
        ("https://threads.net/@zuck/post/C1abc?xmt=AQ&slof=1", "https://www.threads.net/@zuck/post/C1abc"),
        ("https://www.threads.com/@zuck/post/C1abc/?igshid=1#top", "https://www.threads.com/@zuck/post/C1abc"),
        # Facebook: path-identified forms lose the whole query, host is normalised
        ("https://m.facebook.com/someone/posts/12345?fbclid=IwAR&__tn__=R", "https://www.facebook.com/someone/posts/12345"),
        ("https://web.facebook.com/reel/123456?s=yWDuG2&fs=e", "https://www.facebook.com/reel/123456"),
        ("https://www.facebook.com/share/p/AbCd/?mibextid=xyz", "https://www.facebook.com/share/p/AbCd/"),
        ("https://fb.watch/abcDEF/?mibextid=1", "https://fb.watch/abcDEF/"),
        # Facebook: query-identified forms keep only the identifying parameters, in a fixed order
        ("https://www.facebook.com/watch/?v=123&ref=sharing&mibextid=1", "https://www.facebook.com/watch/?v=123"),
        ("https://www.facebook.com/watch?v=123", "https://www.facebook.com/watch?v=123"),
        ("https://www.facebook.com/photo.php?set=a.1&fbid=999&type=3&__cft__[0]=x", "https://www.facebook.com/photo.php?fbid=999"),
        ("https://www.facebook.com/photo/?fbid=999&set=a.2", "https://www.facebook.com/photo/?fbid=999"),
        ("https://www.facebook.com/permalink.php?id=55&story_fbid=77&__tn__=%2CO", "https://www.facebook.com/permalink.php?story_fbid=77&id=55"),
        ("https://www.facebook.com/story.php?story_fbid=77&id=55&substory_index=0", "https://www.facebook.com/story.php?story_fbid=77&id=55"),
    ],
)
def test_links_are_cleaned_to_the_identifying_part(dirty, clean):
    assert Classify(dirty).url == clean


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/watch/",
        "https://www.facebook.com/watch/?ref=x",
        "https://www.facebook.com/photo.php",
        "https://www.facebook.com/permalink.php?story_fbid=77",
        "https://www.facebook.com/story.php?id=55",
        "https://fb.watch/",
    ],
)
def test_facebook_links_missing_their_identifying_parameters_are_rejected(url):
    assert Classify(url) is None


def test_cleaning_is_idempotent():
    for url in (FB_POST + FB_TAIL, "https://x.com/jack/status/20?s=46", "https://www.facebook.com/permalink.php?id=55&story_fbid=77"):
        once = Classify(url)
        assert Classify(once.url) == once


def test_same_post_with_different_tracking_is_one_link():
    text = f"{FB_POST}{FB_TAIL} and again {FB_POST}?fbclid=IwAR123 and {FB_POST}"
    assert [l.url for l in FindLinks(text)] == [FB_POST]
    text = "https://www.instagram.com/p/AAA/?igsh=1 https://instagram.com/p/AAA?utm_source=x"
    assert len(FindLinks(text)) == 1


def test_facebook_link_with_brackets_in_query_inside_message_text():
    links = FindLinks(f"look {FB_POST}{FB_TAIL} wow")
    assert [l.url for l in links] == [FB_POST]


def test_find_links_order_dedupe_and_punctuation():
    text = "look https://x.com/a/status/1, and (https://www.instagram.com/p/XYZ/). again https://x.com/a/status/1"
    assert FindLinks(text) == [Link("twitter", "https://x.com/a/status/1"), Link("instagram", "https://www.instagram.com/p/XYZ/")]


def test_find_links_includes_hidden_text_links():
    links = FindLinks("click here", ["https://www.threads.net/@a/post/Cx1"])
    assert links == [Link("threads", "https://www.threads.net/@a/post/Cx1")]


def test_find_links_none():
    assert FindLinks("hello https://example.com") == []
