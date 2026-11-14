"""Orchestrates: URL -> fetch -> reader mode -> (archive if only a teaser) -> Telegraph page."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from minionbot.web.archive import ArchiveProvider
from minionbot.translate.articles import Translator, TranslateArticle
from minionbot.translate.language import LANGUAGE_NAMES, IsAlready
from minionbot.web.errors import HttpStatusError, UnexpectedWebError, WebError
from minionbot.web.fetcher import PageFetcher
from minionbot.web.reader import MIN_FULL_ARTICLE_CHARS, MIN_READABLE_CHARS, Article, ExtractArticle, FirstParagraph
from minionbot.web.telegraph import TelegraphClient

LOGGER = logging.getLogger(__name__)

SUMMARY_CHARS = 350
NOTHING_READABLE = "I couldn't find readable content on that page (it may need JavaScript or a login)."


@dataclass
class WebResult:
    sourceUrl: str
    title: str
    siteName: str
    date: str
    summary: str
    text: str  # plain text, used when Telegraph is unavailable
    telegraphUrl: str | None = None
    notes: list[str] = field(default_factory=list)


def Summarize(article: Article) -> str:
    text = (article.description or FirstParagraph(article.nodes) or article.text.split("\n\n", 1)[0]).strip()
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1].rsplit(" ", 1)[0] + "…"


class WebService:
    def __init__(
        self,
        fetcher: PageFetcher,
        telegraph: TelegraphClient | None,
        archives: list[ArchiveProvider] | None = None,
        translator: Translator | None = None,
    ):
        self.translator = translator
        self.fetcher = fetcher
        self.telegraph = telegraph
        self.archives = archives or []

    async def Close(self) -> None:
        await self.fetcher.Close()
        if self.telegraph:
            await self.telegraph.Close()
        if self.translator is not None and hasattr(self.translator, "Close"):
            await self.translator.Close()

    async def Read(self, url: str, language: str | None = None) -> WebResult:
        """Reader-mode version of url, translated to `language` ("ru"/"en") if given. Raises WebError."""
        if language and self.translator is None:
            raise WebError("Translation is not configured (the owner needs to set a Gemini API key).")
        try:
            return await self._Read(url, language)
        except WebError:
            raise
        except Exception as error:
            LOGGER.exception("Unexpected failure reading %s", url)
            raise UnexpectedWebError(f"Sorry, something went wrong while reading {url}") from error

    async def _Read(self, url: str, language: str | None = None) -> WebResult:
        notes: list[str] = []
        pageNotes: list[str] = []  # footer of the Telegraph page only
        sourceUrl = url
        try:
            page = await self.fetcher.Fetch(url)
            sourceUrl, article = page.finalUrl, ExtractArticle(page.html, page.finalUrl)
        except HttpStatusError as error:
            if not error.refused or not self.archives:
                raise
            archived, source = await self._FromArchives(url, None, minChars=MIN_READABLE_CHARS * 3)  # refused: any readable copy will do
            if archived is None:
                raise WebError(f"{error} I couldn't find an archived copy either.") from error
            article, notes = archived, [f"The site refused access, so this is the archived copy from {source}."]
        else:
            if article is None or article.paywalled:
                better, source = await self._FromArchives(url, article)
                if better is not None:
                    article, notes = better, [f"Only a teaser was served, so this is the archived copy from {source}."]
                elif article is not None:
                    notes = ["Only a teaser was available; the full text is behind a paywall."]
        if article is None:
            raise WebError(NOTHING_READABLE)
        if language:
            name = LANGUAGE_NAMES[language]
            if IsAlready(article.text, language):
                notes.append(f"The page is already in {name}, so it was not translated.")
            else:
                article, cut = await TranslateArticle(article, self.translator, language)
                pageNotes.append(f"Machine translation to {name} (Gemini). The original is at the source link below.")
                if cut:
                    notes.append("Only the first part of this long page was translated.")
        result = WebResult(
            sourceUrl=sourceUrl,
            title=article.title,
            siteName=article.siteName or urlsplit(sourceUrl).hostname or "",
            date=article.date,
            summary=Summarize(article),
            text=article.text,
            notes=notes,
        )
        if self.telegraph is not None:
            try:
                result.telegraphUrl = await self.telegraph.CreatePage(
                    article.title, article.author or result.siteName, sourceUrl, self._WithSource(article, sourceUrl, notes + pageNotes), sourceUrl
                )
            except WebError as error:
                LOGGER.warning("Telegraph unavailable (%s); sending text instead", error)
        return result

    @staticmethod
    def _WithSource(article: Article, sourceUrl: str, notes: list[str]) -> list:
        footer = [{"tag": "p", "children": [{"tag": "em", "children": [note]}]} for note in notes]
        footer.append({"tag": "p", "children": ["Source: ", {"tag": "a", "attrs": {"href": sourceUrl}, "children": [sourceUrl]}]})
        return [*article.nodes, *footer]

    async def _FromArchives(self, url: str, current: Article | None, minChars: int = MIN_FULL_ARTICLE_CHARS) -> tuple[Article | None, str]:
        """The first archived copy that has clearly more text than what the site served."""
        have = len(current.text) if current else 0
        for provider in self.archives:
            try:
                archived = await provider.Find(url)
                if not archived:
                    continue
                page = await self.fetcher.Fetch(archived)
                article = ExtractArticle(page.html, page.finalUrl)
            except WebError as error:
                LOGGER.info("Archive %s gave nothing for %s: %s", provider.name, url, error)
                continue
            if article is not None and len(article.text) >= max(minChars, have * 1.5):
                article.paywalled = False
                return article, provider.name
        return None, ""
