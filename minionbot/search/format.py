"""Telegram messages (HTML) for search results."""

from __future__ import annotations

import html
from urllib.parse import urlsplit

from minionbot.search.models import AiAnswer, OrganicResult, SearchResult

MESSAGE_LIMIT = 3900  # Telegram allows 4096 characters


def Escape(text: str) -> str:
    return html.escape(text, quote=False)


def Link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{Escape(text)}</a>'


def Domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def FormatAi(ai: AiAnswer) -> str:
    lines = ["<b>AI overview (Google)</b>"]
    for block in ai.blocks:
        if block.kind == "h":
            lines += ["", f"<b>{Escape(block.text)}</b>"]
        elif block.kind == "li":
            lead = f"<b>{Escape(block.title)}</b>" if block.title else ""
            body = Escape(block.text)
            lines.append("• " + (f"{lead}: {body}" if lead and body else lead or body))
        else:
            lines += ["", Escape(block.text)]
    if ai.sources:
        lines += ["", "<b>Sources</b>"] + [f"{number}. {Link(source.link, source.title)}" for number, source in enumerate(ai.sources, start=1)]
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def FormatOrganic(query: str, results: list[OrganicResult]) -> str:
    lines = [f"<b>Google: {Escape(query)}</b>"]
    for number, result in enumerate(results, start=1):
        lines += ["", f"{number}. {Link(result.link, result.title)}", Escape(result.displayedLink or Domain(result.link))]
        if result.snippet:
            lines.append(Escape(result.snippet))
    return "\n".join(lines)


def Chunk(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on line boundaries so no HTML tag (they never span lines) is cut in half."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def FormatResult(result: SearchResult) -> list[str]:
    """Messages to send, in order: the AI overview (if any), then the organic results."""
    messages: list[str] = []
    if result.ai:
        messages += Chunk(FormatAi(result.ai))
    if result.organic:
        messages += Chunk(FormatOrganic(result.query, result.organic))
    if not messages:
        messages.append(f"No results for “{Escape(result.query)}”.")
    return messages
