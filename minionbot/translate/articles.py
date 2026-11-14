"""Translate an Article block by block, keeping its structure (headings, lists, quotes, images, links, emphasis)."""

from __future__ import annotations

import copy
import dataclasses
import html
import re
from typing import Protocol

from lxml import html as lxml_html

from minionbot.web.reader import Article, Node, NodesToText

MAX_TRANSLATE_CHARS = 30_000  # protects the free quota; longer pages are translated up to here and then cut
UNIT_TAGS = {"p", "h3", "h4", "li", "figcaption", "blockquote", "aside"}
INLINE_TAGS = {"a", "strong", "em", "code", "s", "u", "br"}
SKIPPED_TAGS = {"pre", "figure_image"}
FORMATTING = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "code": "code", "s": "s", "u": "u"}


class Translator(Protocol):
    async def TranslateMany(self, strings: list[str], language: str) -> list[str]: ...


def IsInline(node: Node) -> bool:
    return isinstance(node, str) or node.get("tag") in INLINE_TAGS


def IsUnit(node: Node) -> bool:
    """A block whose content is only text and inline formatting: it is translated as one piece."""
    return isinstance(node, dict) and node["tag"] in UNIT_TAGS and bool(node.get("children")) and all(IsInline(c) for c in node["children"])


def CollectUnits(nodes: list[Node]) -> list[dict]:
    """The translatable blocks in document order (code blocks and images are left alone)."""
    units: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict) or node["tag"] in SKIPPED_TAGS:
            continue
        if IsUnit(node):
            units.append(node)
        elif node["tag"] != "pre":
            units += CollectUnits(node.get("children", []))
    return units


def ToMarkup(children: list[Node]) -> str:
    parts = []
    for child in children:
        if isinstance(child, str):
            parts.append(html.escape(child, quote=False))
        elif child["tag"] == "br":
            parts.append("<br/>")
        else:
            attrs = f' href="{html.escape(child["attrs"]["href"], quote=True)}"' if child["tag"] == "a" and child.get("attrs") else ""
            parts.append(f'<{child["tag"]}{attrs}>{ToMarkup(child.get("children", []))}</{child["tag"]}>')
    return "".join(parts)


def Hrefs(children: list[Node]) -> set[str]:
    found: set[str] = set()
    for child in children:
        if isinstance(child, dict):
            if child["tag"] == "a" and child.get("attrs"):
                found.add(child["attrs"]["href"])
            found |= Hrefs(child.get("children", []))
    return found


def FromElement(element, allowedHrefs: set[str]) -> list[Node]:
    nodes: list[Node] = []
    if element.text:
        nodes.append(re.sub(r"\s+", " ", element.text))
    for child in element:
        tag = child.tag if isinstance(child.tag, str) else ""
        inner = FromElement(child, allowedHrefs)
        if tag == "br":
            nodes.append({"tag": "br"})
        elif tag in FORMATTING and inner:
            nodes.append({"tag": FORMATTING[tag], "children": inner})
        elif tag == "a" and inner and child.get("href") in allowedHrefs:  # the model may not invent links
            nodes.append({"tag": "a", "attrs": {"href": child.get("href")}, "children": inner})
        else:
            nodes += inner  # unknown or disallowed tag: keep the text, drop the wrapper
        if child.tail:
            nodes.append(re.sub(r"\s+", " ", child.tail))
    return nodes


def FromMarkup(markup: str, allowedHrefs: set[str]) -> list[Node]:
    """Translated markup -> nodes. Only the source's own links and simple formatting survive."""
    try:
        wrapper = lxml_html.fragment_fromstring(markup, create_parent="div")
    except (ValueError, lxml_html.etree.ParserError):
        return []
    nodes = FromElement(wrapper, allowedHrefs)
    return nodes if NodesToText([{"tag": "p", "children": nodes}]).strip() else []


def Cut(nodes: list[Node], maxChars: int) -> tuple[list[Node], bool]:
    """Whole top-level blocks up to about maxChars of translatable text (always at least the first block)."""
    kept: list[Node] = []
    total = 0
    for node in nodes:
        size = sum(len(NodesToText([unit])) for unit in CollectUnits([node]))
        if kept and total + size > maxChars:
            return kept, True
        kept.append(node)
        total += size
    return kept, False


async def TranslateArticle(article: Article, translator: Translator, language: str) -> tuple[Article, bool]:
    """A translated copy of the article and whether a long page was cut. Raises TranslationError."""
    nodes, truncated = Cut(copy.deepcopy(article.nodes), MAX_TRANSLATE_CHARS)
    units = CollectUnits(nodes)
    sources = [html.escape(article.title, quote=False)] + [ToMarkup(unit["children"]) for unit in units]
    translated = await translator.TranslateMany(sources, language)
    titleNodes = FromMarkup(translated[0], set())
    title = NodesToText([{"tag": "p", "children": titleNodes}]).strip() or article.title
    for unit, markup in zip(units, translated[1:]):
        rebuilt = FromMarkup(markup, Hrefs(unit["children"]))
        if rebuilt:  # an empty answer for a non-empty block keeps the original text instead of losing it
            unit["children"] = rebuilt
    text = NodesToText(nodes)
    return dataclasses.replace(article, title=title[:256], nodes=nodes, text=text, description=""), truncated
