import json

import pytest

from minionbot.web.reader import (
    MIN_FULL_ARTICLE_CHARS,
    ExtractArticle,
    HtmlToNodes,
    LooksPaywalled,
    NodesToText,
    TrimNodes,
)

from tests.web_fixtures import PARAGRAPHS, PAYWALL_MARKER, TEASER, Article

URL = "https://news.example.com/2026/riverfront"


def Tags(nodes):
    found = []

    def Walk(items):
        for node in items:
            if isinstance(node, dict):
                found.append(node["tag"])
                Walk(node.get("children", []))

    Walk(nodes)
    return found


def test_extracts_main_content_without_ads_menus_banners_and_footer():
    article = ExtractArticle(Article(), URL)
    assert article is not None
    for paragraph in PARAGRAPHS:
        assert paragraph[:60] in article.text
    for junk in ["BUY CHEAP WATCHES", "Accept all cookies", "Log in", "SIDEBAR-TEASER", "FOOTER-LEGAL", "BUYNOW-SCRIPT"]:
        assert junk not in article.text, junk


def test_metadata():
    article = ExtractArticle(Article(), URL)
    assert article.title == "Council approves riverfront plan"
    assert article.siteName == "Example News" and article.author == "Jane Reporter" and article.date.startswith("2026-11-09")
    assert article.description.startswith("After a decade") and article.image == "https://cdn.example.com/lead.jpg"
    assert not article.paywalled


def test_structure_is_kept_and_urls_are_made_absolute():
    nodes = ExtractArticle(Article(), URL).nodes
    tags = Tags(nodes)
    assert "p" in tags and "blockquote" in tags and "ul" in tags and "li" in tags and "a" in tags
    dump = json.dumps(nodes)
    assert "https://news.example.com/plan.pdf" in dump  # relative link resolved
    assert "javascript:" not in dump  # unsafe links lose their wrapper
    assert "https://news.example.com/images/river.jpg" in dump  # in-article image kept and absolute


def test_lead_image_is_added_when_the_article_has_none():
    html = Article().replace('<figure><img src="/images/river.jpg" alt="The river at dusk"><figcaption>The river at dusk</figcaption></figure>', "")
    nodes = ExtractArticle(html, URL).nodes
    assert nodes[0] == {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": "https://cdn.example.com/lead.jpg"}}]}


def test_nothing_readable_returns_none():
    assert ExtractArticle("<html><body><nav>menu</nav></body></html>", URL) is None
    assert ExtractArticle("", URL) is None


def test_paywall_teaser_is_flagged():
    article = ExtractArticle(TEASER, URL)
    assert article is not None and len(article.text) < MIN_FULL_ARTICLE_CHARS and article.paywalled


def test_a_long_article_with_a_soft_paywall_marker_is_not_a_teaser():
    # many sites mark articles as "not free" but still ship the whole text: no archive needed
    full = Article(extraHead=PAYWALL_MARKER, paragraphs=PARAGRAPHS * 2)
    article = ExtractArticle(full, URL)
    assert len(article.text) >= MIN_FULL_ARTICLE_CHARS and not article.paywalled


def test_short_page_without_paywall_markers_is_not_flagged():
    assert not ExtractArticle(Article(paragraphs=[PARAGRAPHS[0]]), URL).paywalled


@pytest.mark.parametrize(
    "html",
    [
        '<script type="application/ld+json">{"isAccessibleForFree": "False"}</script>',
        '<div class="article-paywall">x</div>',
        "<p>Subscribe to continue reading</p>",
        "<p>Только для подписчиков</p>",
        "<p>This content is for subscribers only.</p>",
    ],
)
def test_paywall_markers(html):
    assert LooksPaywalled(html)


def test_no_paywall_markers():
    assert not LooksPaywalled("<p>An ordinary article about subscription models in economics.</p>")


# ---- HTML -> Telegraph nodes -----------------------------------------------------------------------

def Convert(fragment):
    return HtmlToNodes(fragment, "https://site.example/dir/page")


def test_headings_formatting_and_lists_map_to_telegraph_tags():
    nodes = Convert("<h1>A</h1><h2>B</h2><h5>C</h5><p><b>bold</b> <i>it</i> <em>em</em></p><ol><li>one</li></ol><pre>code</pre>")
    assert Tags(nodes) == ["h3", "h3", "h4", "p", "strong", "em", "em", "ol", "li", "pre"]


def test_unknown_tags_are_unwrapped_and_scripts_dropped():
    nodes = Convert("<div><section><span>kept <u>text</u></span><script>evil()</script><style>x{}</style></section></div>")
    assert NodesToText(nodes) == "kept text" and "evil" not in json.dumps(nodes)


def test_links_are_made_absolute_and_unsafe_ones_unwrapped():
    nodes = Convert('<p><a href="../other">rel</a> <a href="mailto:a@b.c">mail</a> <a href="javascript:x()">js</a> <a href="data:text/html,x">data</a></p>')
    dump = json.dumps(nodes)
    assert "https://site.example/other" in dump and "mailto:a@b.c" in dump
    assert "javascript" not in dump and "data:text" not in dump and "js" in NodesToText(nodes)


def test_images_become_figures_and_bad_sources_are_dropped():
    nodes = Convert('<p>text</p><img src="/a.png"><img src="data:image/gif;base64,AAA"><img src="">')
    assert [n["tag"] for n in nodes] == ["p", "figure"]
    assert nodes[1]["children"][0]["attrs"]["src"] == "https://site.example/a.png"


def test_loose_text_is_wrapped_in_paragraphs():
    nodes = Convert("<div>loose text <b>bold</b></div><p>para</p>trailing")
    assert [n["tag"] for n in nodes] == ["p", "p", "p"]


def test_empty_and_invalid_input():
    assert Convert("") == [] and Convert("<p></p>") == []


def test_nodes_to_text():
    nodes = Convert("<h1>Title</h1><p>one <b>two</b></p><ul><li>a</li><li>b</li></ul><figure><img src='https://x/y.png'></figure>")
    assert NodesToText(nodes) == "Title\n\none two\n\n• a\n• b"


# ---- Telegraph size limit -----------------------------------------------------------------------------

def test_trim_nodes_keeps_what_fits():
    nodes = [{"tag": "p", "children": ["x" * 100]} for _ in range(50)]
    kept, trimmed = TrimNodes(nodes, 1000)
    assert trimmed and 0 < len(kept) < 50 and len(json.dumps(kept).encode()) <= 1000
    assert TrimNodes(nodes, 10**7) == (nodes, False)
