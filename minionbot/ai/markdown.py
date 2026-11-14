"""Gemini answers in Markdown; Telegram shows Markdown symbols literally, so convert the common subset to
Telegram's HTML (bold, italic, code, pre, links, quotes, lists) and split long answers into message-sized chunks.

Everything that comes from the model is HTML-escaped first, and the result is checked for balanced tags; if anything
looks off the chunk is sent as escaped plain text, so a formatting problem can never break or inject markup.
"""

from __future__ import annotations

import html
import re

CHUNK_CHARS = 2800  # Telegram allows 4096; HTML tags and escaping add to the Markdown length
FENCE = re.compile(r"^\s*```(\S*)\s*$")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)")
BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
ITALIC_STAR = re.compile(r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])")
ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])")
STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
TAGS = {"b", "i", "s", "u", "code", "pre", "a", "blockquote"}


def Escape(text: str) -> str:
    return html.escape(text, quote=False)


def Inline(text: str) -> str:
    """Inline Markdown of one line -> HTML (code spans are protected from the other rules)."""
    spans: list[str] = []

    def Keep(match: re.Match) -> str:
        spans.append(f"<code>{Escape(match.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = INLINE_CODE.sub(Keep, text)
    text = Escape(text)
    text = LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>', text)
    text = BOLD.sub(lambda m: f"<b>{m.group(2)}</b>", text)
    text = STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = ITALIC_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = ITALIC_UNDERSCORE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def IsBalanced(markup: str) -> bool:
    stack: list[str] = []
    for closing, name in re.findall(r"<(/?)([a-z]+)[^>]*>", markup):
        if name not in TAGS:
            return False
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def ToHtml(markdown: str) -> str:
    """Telegram HTML for a chunk of Markdown. Falls back to escaped plain text if the result would be malformed."""
    out: list[str] = []
    lines = markdown.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        fence = FENCE.match(line)
        if fence:  # fenced code block: verbatim
            index += 1
            code: list[str] = []
            while index < len(lines) and not FENCE.match(lines[index]):
                code.append(lines[index])
                index += 1
            out.append(f"<pre>{Escape(chr(10).join(code))}</pre>")
            index += 1
            continue
        if line.lstrip().startswith("|"):  # table: monospaced, as Telegram has no tables
            rows = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(lines[index])
                index += 1
            out.append(f"<pre>{Escape(chr(10).join(rows))}</pre>")
            continue
        if line.startswith(">"):
            quote = []
            while index < len(lines) and lines[index].startswith(">"):
                quote.append(Inline(lines[index][1:].lstrip()))
                index += 1
            out.append(f"<blockquote>{chr(10).join(quote)}</blockquote>")
            continue
        heading, bullet = HEADING.match(line), BULLET.match(line)
        if RULE.match(line):
            out.append("———")
        elif heading:
            out.append(f"<b>{Inline(heading.group(1))}</b>")
        elif bullet:
            out.append(f"{bullet.group(1)}• {Inline(bullet.group(2))}")
        else:
            out.append(Inline(line))
        index += 1
    result = "\n".join(out)
    return result if IsBalanced(result) else Escape(markdown)


def SplitMarkdown(markdown: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Message-sized pieces, cut at line boundaries; a code fence cut in two is closed and reopened."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    fence: str | None = None  # the opening fence line while inside a code block

    def Flush() -> None:
        nonlocal current, size
        if current:
            chunks.append("\n".join(current + (["```"] if fence else [])))
        current, size = ([fence] if fence else []), (len(fence) if fence else 0)

    for line in markdown.split("\n"):
        pieces = [line[i : i + limit] for i in range(0, len(line), limit)] or [""]  # a single huge line
        for piece in pieces:
            if current and size + len(piece) + 1 > limit and size > (len(fence) if fence else 0):
                Flush()
            current.append(piece)
            size += len(piece) + 1
        match = FENCE.match(line)
        if match:
            fence = None if fence else line.strip()
    if any(part.strip() for part in current):
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]
