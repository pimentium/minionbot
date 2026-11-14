import json

import pytest

from minionbot.translate import articles
from minionbot.translate.articles import CollectUnits, FromMarkup, Hrefs, ToMarkup, TranslateArticle
from minionbot.web.errors import TranslationError
from minionbot.web.reader import Article, NodesToText

LINK = "https://source.example/plan"


def P(*children):
    return {"tag": "p", "children": list(children)}


def Rich():
    return [
        {"tag": "h3", "children": ["Heading one"]},
        {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": "https://cdn.example/a.jpg"}}]},
        P("Plain paragraph with & ampersand and <angle> brackets."),
        P("Read the ", {"tag": "a", "attrs": {"href": LINK}, "children": ["full ", {"tag": "strong", "children": ["plan"]}]}, " today", {"tag": "br"}, "and ", {"tag": "em", "children": ["enjoy"]}, "."),
        {"tag": "blockquote", "children": ["Quoted words."]},
        {"tag": "ul", "children": [{"tag": "li", "children": ["First item"]}, {"tag": "li", "children": ["Second ", {"tag": "code", "children": ["code"]}]}]},
        {"tag": "pre", "children": ["print('keep me')"]},
        {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": "https://cdn.example/b.jpg"}}, {"tag": "figcaption", "children": ["A caption"]}]},
        {"tag": "blockquote", "children": [P("Nested quote paragraph.")]},
    ]


def MakeArticle(nodes=None, title="Original title"):
    nodes = Rich() if nodes is None else nodes
    return Article(title=title, nodes=nodes, text=NodesToText(nodes), siteName="Example", author="Jane", date="2026-11-09", description="Original description", image="")


class Fake:
    """A pretend translator: wraps every fragment so tests can see what was translated (keeps tags intact)."""

    def __init__(self, transform=None, error=None):
        self.transform, self.error, self.calls = transform or (lambda s: f"RU[{s}]"), error, []

    async def TranslateMany(self, strings, language):
        self.calls.append((list(strings), language))
        if self.error:
            raise self.error
        return [self.transform(s) for s in strings]


# ---- markup round trip -----------------------------------------------------------------------------------------

def test_inline_markup_round_trips_with_escaping():
    unit = Rich()[3]
    markup = ToMarkup(unit["children"])
    assert markup == f'Read the <a href="{LINK}">full <strong>plan</strong></a> today<br/>and <em>enjoy</em>.'
    assert FromMarkup(markup, {LINK}) == unit["children"]
    plain = ToMarkup(Rich()[2]["children"])
    assert plain == "Plain paragraph with &amp; ampersand and &lt;angle&gt; brackets."
    assert FromMarkup(plain, set()) == Rich()[2]["children"]


def test_the_model_cannot_invent_links_or_tags():
    nodes = FromMarkup('See <a href="https://evil.example/">this</a> and <a href="javascript:x()">that</a> <script>x()</script><b>bold</b> <span>text</span>', set())
    dump = json.dumps(nodes)
    assert "evil.example" not in dump and "javascript" not in dump and "<script" not in dump
    assert NodesToText([P(*nodes)]) == "See this and that x()bold text" or "bold" in NodesToText([P(*nodes)])
    assert {"tag": "strong", "children": ["bold"]} in nodes


def test_only_the_sources_own_links_survive():
    nodes = FromMarkup(f'<a href="{LINK}">ok</a> <a href="https://new.example/">new</a>', {LINK})
    assert [n["attrs"]["href"] for n in nodes if isinstance(n, dict) and n["tag"] == "a"] == [LINK]


@pytest.mark.parametrize("markup", ["", "   ", "<b></b>", "<a href='x'></a>"])
def test_empty_translations_are_rejected(markup):
    assert FromMarkup(markup, set()) == []


def test_hrefs_are_collected_recursively():
    assert Hrefs(Rich()[3]["children"]) == {LINK}


# ---- what gets translated ----------------------------------------------------------------------------------------

def test_units_are_the_text_blocks_without_code_and_images():
    texts = [NodesToText([u]) for u in CollectUnits(Rich())]
    assert texts == ["Heading one", "Plain paragraph with & ampersand and <angle> brackets.", "Read the full plan today\nand enjoy.",
                     "Quoted words.", "First item", "Second code", "A caption", "Nested quote paragraph."]


# ---- translating an article ----------------------------------------------------------------------------------------

async def test_structure_links_and_images_survive_translation():
    original = MakeArticle()
    snapshot = json.dumps(original.nodes)
    translator = Fake()
    result, cut = await TranslateArticle(original, translator, "ru")
    assert not cut and json.dumps(original.nodes) == snapshot  # the input is not modified
    assert result.title == "RU[Original title]"
    assert [n["tag"] for n in result.nodes] == [n["tag"] for n in original.nodes]
    paragraph = result.nodes[3]
    assert paragraph["children"][0] == "RU[Read the " and paragraph["children"][1]["attrs"]["href"] == LINK
    assert result.nodes[1] == original.nodes[1] and result.nodes[6] == original.nodes[6]  # image and code untouched
    assert result.nodes[7]["children"][1]["children"] == ["RU[A caption]"]
    assert "RU[Quoted words.]" in result.text and "print('keep me')" in result.text  # code stays verbatim
    assert (result.siteName, result.author, result.date) == ("Example", "Jane", "2026-11-09") and result.description == ""
    assert translator.calls[0][1] == "ru" and len(translator.calls) == 1 and translator.calls[0][0][0] == "Original title"


async def test_escaped_text_is_unescaped_again_in_the_result():
    result, _ = await TranslateArticle(MakeArticle([P("Fish & chips <3")], title="A & B"), Fake(lambda s: s), "en")
    assert result.nodes[0]["children"] == ["Fish & chips <3"] and result.title == "A & B"


async def test_an_empty_answer_keeps_the_original_text_of_that_block():
    result, _ = await TranslateArticle(MakeArticle([P("Keep me"), P("Translate me")]), Fake(lambda s: "" if s == "Keep me" else s.upper()), "ru")
    assert [NodesToText([n]) for n in result.nodes] == ["Keep me", "TRANSLATE ME"]


async def test_a_mangled_link_in_the_answer_loses_only_the_link():
    evil = lambda s: s.replace(LINK, "https://evil.example/")
    result, _ = await TranslateArticle(MakeArticle([P("Go ", {"tag": "a", "attrs": {"href": LINK}, "children": ["there"]})]), Fake(evil), "ru")
    assert "evil.example" not in json.dumps(result.nodes) and NodesToText(result.nodes) == "Go there"


async def test_long_pages_are_cut_at_a_block_boundary(monkeypatch):
    monkeypatch.setattr(articles, "MAX_TRANSLATE_CHARS", 50)
    nodes = [P("a" * 30), P("b" * 30), P("c" * 30)]
    translator = Fake()
    result, cut = await TranslateArticle(MakeArticle(nodes), translator, "ru")
    assert cut and len(result.nodes) == 1 and len(translator.calls[0][0]) == 2  # title + the one block


async def test_the_first_block_is_always_kept_even_if_it_is_huge(monkeypatch):
    monkeypatch.setattr(articles, "MAX_TRANSLATE_CHARS", 10)
    result, cut = await TranslateArticle(MakeArticle([P("x" * 500), P("y")]), Fake(), "ru")
    assert cut and len(result.nodes) == 1


async def test_translation_errors_propagate():
    with pytest.raises(TranslationError, match="busy"):
        await TranslateArticle(MakeArticle(), Fake(error=TranslationError("busy")), "ru")
