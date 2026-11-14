"""Cheap detection of "this text is already in the target language", so no translation quota is spent on it."""

from __future__ import annotations

import re

LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}
ENGLISH_WORDS = {
    "the", "and", "of", "to", "in", "is", "that", "for", "it", "with", "as", "on", "was", "are", "this", "be", "by",
    "have", "not", "from", "at", "or", "an", "which", "but", "you", "will", "has", "their", "they", "would",
}
SAMPLE_CHARS = 4000
MIN_LETTERS = 80


def IsAlready(text: str, language: str) -> bool:
    sample = text[:SAMPLE_CHARS]
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return False
    cyrillic = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    if language == "ru":
        ukrainian = sum(1 for c in sample.lower() if c in "іїєґ")
        russianOnly = sum(1 for c in sample.lower() if c in "ыэъ")
        return cyrillic / len(letters) >= 0.7 and russianOnly > 0 and ukrainian <= len(letters) * 0.002
    if language == "en":
        latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
        words = re.findall(r"[a-z']+", sample.lower())
        return latin / len(letters) >= 0.9 and bool(words) and sum(1 for w in words if w in ENGLISH_WORDS) / len(words) >= 0.12
    return False
