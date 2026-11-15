"""Translation of plain text (a /rus or /eng message without a link): line by line, line breaks kept."""

from __future__ import annotations

import html
import re

from minionbot.translate.articles import Translator
from minionbot.web.errors import TranslationError

TAG = re.compile(r"<[^>]*>")


async def TranslateText(text: str, translator: Translator, language: str) -> str:
    """The text translated; empty lines and the line structure stay. Raises TranslationError.

    The text is untrusted: it is sent as escaped fragments (the same prompt as page translation, which says to
    translate only), and the answer is reduced to plain text again (no tags survive).
    """
    lines = text.splitlines()
    indexes = [i for i, line in enumerate(lines) if line.strip()]
    if not indexes:
        raise TranslationError("There is nothing to translate.")
    translated = await translator.TranslateMany([html.escape(lines[i].strip(), quote=False) for i in indexes], language)
    if len(translated) != len(indexes):
        raise TranslationError("The translation came back malformed.")
    for i, fragment in zip(indexes, translated):
        lines[i] = html.unescape(TAG.sub("", fragment)).strip() or lines[i]  # an empty answer keeps the original line
    return "\n".join(lines).strip()
