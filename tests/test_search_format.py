import re

from minionbot.search.format import MESSAGE_LIMIT, Chunk, Domain, Escape, FormatAi, FormatOrganic, FormatResult
from minionbot.search.models import AiAnswer, AiBlock, AiSource, OrganicResult, SearchResult
from minionbot.search.serpapi import ParseAiOverview, ParseOrganic

from tests.search_fixtures import AI_OVERVIEW, Organic


def Balanced(text):
    """Every tag we emit is closed on the same message (Telegram rejects broken HTML)."""
    return all(text.count(f"<{tag}") == text.count(f"</{tag}>") for tag in ("b", "a"))


def test_organic_results_are_numbered_with_link_domain_and_snippet():
    text = FormatOrganic("python", ParseOrganic({"organic_results": Organic(2)}))
    assert text.splitlines()[0] == "<b>Google: python</b>"
    assert '1. <a href="https://www.example1.com/page">Result 1 - Example</a>' in text
    assert "Snippet for result 1." in text and "2. <a" in text and Balanced(text)


def test_displayed_link_falls_back_to_the_domain_and_snippet_is_optional():
    text = FormatOrganic("q", [OrganicResult("T", "https://www.example.org/a/b")])
    assert text.splitlines()[3] == "example.org" and len(text.splitlines()) == 4
    assert Domain("https://news.example.org:8080/x") == "news.example.org"


def test_everything_from_the_internet_is_escaped():
    result = OrganicResult('<script>alert("x")</script> & co', 'https://e.com/a?x="1"&y=<2>', "a < b > c & d")
    text = FormatOrganic("<q&>", [result])
    assert "<script>" not in text and "&lt;script&gt;" in text and "&amp; co" in text
    assert 'href="https://e.com/a?x=&quot;1&quot;&amp;y=&lt;2&gt;"' in text
    assert "<b>Google: &lt;q&amp;&gt;</b>" in text and "a &lt; b &gt; c &amp; d" in text
    assert Balanced(text)


def test_ai_overview_layout():
    text = FormatAi(ParseAiOverview(AI_OVERVIEW))
    assert text.splitlines()[0] == "<b>AI overview (Google)</b>"
    assert "<b>What is Python?</b>" in text and "Python is a high-level programming language." in text
    assert "• <b>Readable</b>: The syntax is close to plain English." in text and "• No lead-in here." in text
    assert '<b>Sources</b>\n1. <a href="https://www.python.org/">Python.org</a>' in text
    assert "\n\n\n" not in text and Balanced(text)


def test_ai_overview_without_sources_and_list_items_with_only_a_title():
    ai = AiAnswer([AiBlock("p", "Text"), AiBlock("li", "", "Only title")])
    text = FormatAi(ai)
    assert "Sources" not in text and "• <b>Only title</b>" in text


def test_ai_comes_first_then_the_results():
    result = SearchResult("q", ParseOrganic({"organic_results": Organic(2)}), ParseAiOverview(AI_OVERVIEW))
    messages = FormatResult(result)
    assert len(messages) == 2 and messages[0].startswith("<b>AI overview") and messages[1].startswith("<b>Google: q")


def test_without_ai_only_the_results_and_with_nothing_a_friendly_note():
    assert len(FormatResult(SearchResult("q", ParseOrganic({"organic_results": Organic(1)})))) == 1
    assert FormatResult(SearchResult("a <b> query")) == ["No results for “a &lt;b&gt; query”."]


def test_long_results_are_split_on_line_boundaries_within_the_limit():
    results = [OrganicResult(f"Title {i}", f"https://e{i}.example/x", "snippet " * 50) for i in range(40)]
    messages = FormatResult(SearchResult("q", results))
    assert len(messages) > 1 and all(len(m) <= MESSAGE_LIMIT for m in messages) and all(Balanced(m) for m in messages)
    assert "".join(re.findall(r"Title \d+", " ".join(messages))) == "".join(f"Title {i}" for i in range(40))


def test_chunk_keeps_short_text_whole_and_handles_empty():
    assert Chunk("a\nb") == ["a\nb"] and Chunk("") == []


def test_escape_leaves_quotes_alone_for_text_nodes():
    assert Escape('"quoted" & <b>') == '"quoted" &amp; &lt;b&gt;'
