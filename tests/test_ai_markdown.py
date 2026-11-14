import pytest

from minionbot.ai.markdown import CHUNK_CHARS, IsBalanced, SplitMarkdown, ToHtml


@pytest.mark.parametrize("markdown,html", [
    ("**bold** and __also bold__", "<b>bold</b> and <b>also bold</b>"),
    ("*italic* and _also italic_", "<i>italic</i> and <i>also italic</i>"),
    ("~~gone~~", "<s>gone</s>"),
    ("***both***", None),  # just must stay valid, whatever the exact nesting
    ("`code <b>`", "<code>code &lt;b&gt;</code>"),
    ("# Heading\n## Sub", "<b>Heading</b>\n<b>Sub</b>"),
    ("- one\n  - nested\n* two\n+ three", "• one\n  • nested\n• two\n• three"),
    ("1. first\n2. second", "1. first\n2. second"),
    ("> quote\n> more", "<blockquote>quote\nmore</blockquote>"),
    ("above\n---\nbelow", "above\n———\nbelow"),
    ("[text](https://example.com/a?x=1&y=2)", '<a href="https://example.com/a?x=1&amp;y=2">text</a>'),
    ("plain & <tags> stay text", "plain &amp; &lt;tags&gt; stay text"),
    ("snake_case_name and 2*3*4 and a * b", "snake_case_name and 2*3*4 and a * b"),
])
def test_markdown_rules(markdown, html):
    result = ToHtml(markdown)
    assert IsBalanced(result)
    if html is not None:
        assert result == html


def test_fenced_code_is_verbatim_and_escaped():
    assert ToHtml('```python\nprint("<hi>" & **x**)\n  indented\n```') == '<pre>print("&lt;hi&gt;" &amp; **x**)\n  indented</pre>'


def test_an_unclosed_fence_still_yields_valid_html():
    assert ToHtml("```\ncode without end") == "<pre>code without end</pre>"


def test_tables_become_monospaced_blocks():
    assert ToHtml("| a | b |\n|---|---|\n| 1 | 2 |") == "<pre>| a | b |\n|---|---|\n| 1 | 2 |</pre>"


def test_formatting_is_not_applied_inside_code_spans():
    assert ToHtml("use `**not bold**` here") == "use <code>**not bold**</code> here"


@pytest.mark.parametrize("link", ["javascript:alert(1)", "data:text/html,x", "ftp://x.example/a", "//evil.example"])
def test_only_http_and_mailto_links_become_links(link):
    assert "<a " not in ToHtml(f"[click]({link})")


def test_script_and_attribute_injection_is_neutralised():
    result = ToHtml('<script>alert(1)</script> <img src=x onerror=alert(1)> [a"b](https://e.com/"onmouseover="x)')
    assert "<script>" not in result and "<img" not in result and "&lt;script&gt;" in result
    assert 'onmouseover="x' not in result.replace("&quot;", "")


def test_balance_checker():
    assert IsBalanced("<b>a</b><i>b</i>") and IsBalanced("") and IsBalanced('<a href="x">y</a>')
    assert not IsBalanced("<b>a</i>") and not IsBalanced("<b>a") and not IsBalanced("<marquee>x</marquee>") and not IsBalanced("</b>")


def test_malformed_results_fall_back_to_escaped_plain_text(monkeypatch):
    from minionbot.ai import markdown

    monkeypatch.setattr(markdown, "Inline", lambda text: "<b>" + markdown.Escape(text))  # a bug that would unbalance tags
    assert ToHtml("some *text* & more") == "some *text* &amp; more"


# ---- splitting ----------------------------------------------------------------------------------------------------

def test_short_text_is_one_chunk_and_empty_text_none():
    assert SplitMarkdown("hello\nworld") == ["hello\nworld"] and SplitMarkdown("") == [] and SplitMarkdown("  \n ") == []


def test_long_text_is_cut_on_line_boundaries_without_losing_anything():
    lines = [f"line {i} " + "word " * 20 for i in range(200)]
    chunks = SplitMarkdown("\n".join(lines), limit=1000)
    assert len(chunks) > 5 and all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines


def test_a_code_block_cut_in_two_is_closed_and_reopened():
    code = [f"x{i} = {i}  # " + "c" * 40 for i in range(60)]
    chunks = SplitMarkdown("intro\n```python\n" + "\n".join(code) + "\n```\noutro", limit=500)
    assert len(chunks) > 2
    for chunk in chunks:
        assert sum(1 for line in chunk.split("\n") if line.strip().startswith("```")) % 2 == 0, chunk  # balanced fences
        assert IsBalanced(ToHtml(chunk)) and len(ToHtml(chunk)) < 4096
    joined = "\n".join(chunks)
    assert all(line in joined for line in code) and chunks[-1].rstrip().endswith("outro")


def test_one_huge_line_is_hard_split():
    chunks = SplitMarkdown("x" * 7000, limit=2000)
    assert [len(c) for c in chunks] == [2000, 2000, 2000, 1000]


def test_default_chunks_leave_room_for_telegrams_limit():
    text = "\n".join(f"- item **{i}** with <angle> & ampersand" for i in range(400))
    for chunk in SplitMarkdown(text):
        assert len(chunk) <= CHUNK_CHARS and len(ToHtml(chunk)) <= 4096
