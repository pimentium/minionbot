"""Reader mode: main content of a page (no ads, menus, popups) as Telegraph nodes and plain text."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import trafilatura
from lxml import html as lxml_html

LOGGER = logging.getLogger(__name__)

MIN_FULL_ARTICLE_CHARS = 1200  # shorter + paywall markers = probably only a teaser
MIN_READABLE_CHARS = 100  # shorter than this is navigation debris, not an article
# Telegraph accepts only these tags. Everything else is unwrapped (its children are kept).
HEADINGS = {"h1": "h3", "h2": "h3", "h3": "h3", "h4": "h4", "h5": "h4", "h6": "h4"}
SAME = {"p", "a", "blockquote", "ul", "ol", "li", "pre", "code", "br", "hr", "figure", "figcaption", "img", "aside", "s", "u"}
RENAMED = {"b": "strong", "strong": "strong", "i": "em", "em": "em", **HEADINGS}
DROPPED = {"script", "style", "noscript", "iframe", "form", "button", "svg", "nav", "footer", "header", "template"}
PAYWALL_PATTERNS = [
    re.compile(r'"isaccessibleforfree"\s*:\s*(false|"false")', re.I),
    re.compile(r'(?:class|id)="[^"]*paywall', re.I),
    re.compile(
        r"subscribe to (?:continue|read|unlock)|to continue reading|subscribers only|for subscribers|already a subscriber"
        r"|sign in to continue|become a member to (?:read|continue)|only available to (?:subscribers|members)"
        r"|для подписчиков|по подписке|подпишитесь, чтобы",
        re.I,
    ),
]

Node = str | dict


@dataclass
class Article:
    title: str
    nodes: list[Node]
    text: str
    siteName: str = ""
    author: str = ""
    date: str = ""
    description: str = ""
    image: str = ""
    paywalled: bool = False  # markers found AND little text: probably only a teaser
    notes: list[str] = field(default_factory=list)


def LooksPaywalled(html: str) -> bool:
    return any(pattern.search(html) for pattern in PAYWALL_PATTERNS)


def SafeHref(value: str, base: str) -> str | None:
    if not value.strip():
        return None  # urljoin("base", "") would turn an empty source into the page's own address
    absolute = urljoin(base, value.strip())
    return absolute if absolute.lower().startswith(("http://", "https://", "mailto:")) else None


def ConvertElement(element, base: str) -> list[Node]:
    """lxml element -> Telegraph nodes (a list, because unwrapped elements yield several)."""
    tag = element.tag if isinstance(element.tag, str) else ""
    if not tag or tag in DROPPED:
        return []
    children = ConvertChildren(element, base)
    if tag in HEADINGS or tag in RENAMED:
        tag = RENAMED[tag]
    elif tag not in SAME:
        return children  # unknown tag: keep the content, drop the wrapper
    attrs: dict[str, str] = {}
    if tag == "a":
        href = SafeHref(element.get("href", ""), base)
        if not href:
            return children
        attrs["href"] = href
    if tag == "img":
        source = SafeHref(element.get("src", "") or element.get("data-src", ""), base)
        if not source or not source.startswith("http"):
            return []
        return [{"tag": "figure", "children": [{"tag": "img", "attrs": {"src": source}}]}]
    if tag in ("br", "hr"):
        return [{"tag": tag}]
    if not children:
        return []
    node: dict = {"tag": tag, "children": children}
    if attrs:
        node["attrs"] = attrs
    return [node]


def ConvertChildren(element, base: str) -> list[Node]:
    nodes: list[Node] = []
    if element.text and element.text.strip():
        nodes.append(re.sub(r"\s+", " ", element.text))
    for child in element:
        nodes += ConvertElement(child, base)
        if child.tail and child.tail.strip():
            nodes.append(re.sub(r"\s+", " ", child.tail))
    return nodes


BLOCK_TAGS = {"p", "h3", "h4", "blockquote", "ul", "ol", "li", "pre", "figure", "aside"}


def WrapLooseText(nodes: list[Node]) -> list[Node]:
    """Top-level text/inline nodes must live in a paragraph."""
    result: list[Node] = []
    loose: list[Node] = []

    def Flush() -> None:
        if loose and any(isinstance(n, dict) or n.strip() for n in loose):
            result.append({"tag": "p", "children": list(loose)})
        loose.clear()

    for node in nodes:
        if isinstance(node, dict) and node["tag"] in BLOCK_TAGS | {"hr"}:
            Flush()
            result.append(node)
        else:
            loose.append(node)
    Flush()
    return result


def HtmlToNodes(fragment: str, base: str) -> list[Node]:
    try:
        root = lxml_html.fromstring(fragment)
    except (ValueError, lxml_html.etree.ParserError):
        return []
    body = root.find("body") if root.tag == "html" else root
    return WrapLooseText(ConvertChildren(body if body is not None else root, base))


def NodesToText(nodes: list[Node]) -> str:
    """Plain text of nodes, one paragraph per block."""
    def Inline(node: Node) -> str:
        if isinstance(node, str):
            return node
        if node["tag"] == "br":
            return "\n"
        return "".join(Inline(child) for child in node.get("children", []))

    blocks = []
    for node in nodes:
        text = Inline(node).strip()
        if node.get("tag") if isinstance(node, dict) else False:
            if node["tag"] in ("ul", "ol"):
                text = "\n".join("• " + Inline(item).strip() for item in node.get("children", []))
            elif node["tag"] == "figure":
                text = ""
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


def ExtractArticle(html: str, url: str) -> Article | None:
    """Main content of the page, or None when nothing readable was found."""
    body = trafilatura.extract(
        html, url=url, include_images=True, include_links=True, include_formatting=True, output_format="html", favor_recall=True
    )
    if not body:
        return None
    nodes = HtmlToNodes(body, url)
    meta = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    title = (getattr(meta, "title", "") or "").strip()
    nodes = DropTitleHeading(nodes, title)
    text = NodesToText(nodes)
    if len(text) < MIN_READABLE_CHARS:
        return None
    image = (getattr(meta, "image", "") or "").strip()
    if image.startswith("http") and not any(isinstance(n, dict) and n["tag"] == "figure" for n in nodes):
        nodes.insert(0, {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": image}}]})
    article = Article(
        title=title or text.split("\n", 1)[0][:120],
        nodes=nodes,
        text=text,
        siteName=(getattr(meta, "sitename", "") or "").strip(),
        author=(getattr(meta, "author", "") or "").strip(),
        date=(getattr(meta, "date", "") or "").strip(),
        description=(getattr(meta, "description", "") or "").strip(),
        image=image,
    )
    article.paywalled = len(text) < MIN_FULL_ARTICLE_CHARS and LooksPaywalled(html)
    return article


def DropTitleHeading(nodes: list[Node], title: str) -> list[Node]:
    """The page title is shown by Telegraph itself; a first heading that repeats it would be a duplicate."""
    if nodes and isinstance(nodes[0], dict) and nodes[0]["tag"] in ("h3", "h4") and title:
        heading = NodesToText([nodes[0]]).strip().lower()
        if heading and (heading in title.lower() or title.lower() in heading):
            return nodes[1:]
    return nodes


def FirstParagraph(nodes: list[Node], minChars: int = 40) -> str:
    """Text of the first real paragraph (not a heading, list or caption)."""
    for node in nodes:
        if isinstance(node, dict) and node["tag"] == "p":
            text = NodesToText([node]).strip()
            if len(text) >= minChars:
                return text
    return ""


def TrimNodes(nodes: list[Node], limitBytes: int) -> tuple[list[Node], bool]:
    """Drop trailing blocks until the JSON fits Telegraph's content limit. Returns (nodes, wasTrimmed)."""
    trimmed = list(nodes)
    while trimmed and len(json.dumps(trimmed, ensure_ascii=False).encode()) > limitBytes:
        trimmed.pop()
    return trimmed, len(trimmed) < len(nodes)
