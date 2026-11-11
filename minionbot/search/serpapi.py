"""Google search through SerpApi (https://serpapi.com): organic results and, when Google shows one, the AI Overview.

The API key travels in the query string, so nothing from httpx (whose messages contain the URL) may reach a log or
a reply: errors are mapped to our own messages here.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from minionbot.search.models import (
    AiAnswer,
    AiBlock,
    AiSource,
    OrganicResult,
    SearchError,
    SearchResult,
    SearchServiceError,
    UnexpectedSearchError,
)

LOGGER = logging.getLogger(__name__)

ENDPOINT = "https://serpapi.com/search"
MAX_ORGANIC = 6
MAX_SOURCES = 4
MAX_QUERY_CHARS = 300
MAX_AI_BLOCKS = 40


def Text(value: Any, limit: int = 1500) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def ParseOrganic(data: dict) -> list[OrganicResult]:
    results = []
    for item in data.get("organic_results") or []:
        if not isinstance(item, dict):
            continue
        title, link = Text(item.get("title")), item.get("link")
        if title and isinstance(link, str) and link.startswith(("http://", "https://")):
            results.append(OrganicResult(title[:200], link, Text(item.get("snippet"), 400), Text(item.get("displayed_link"), 120)))
        if len(results) >= MAX_ORGANIC:
            break
    return results


def ParseBlocks(blocks: Any, depth: int = 0) -> list[AiBlock]:
    """text_blocks -> paragraphs, headings and list items. Unknown block kinds (tables, carousels) are skipped."""
    parsed: list[AiBlock] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict) or depth > 3:
            continue
        kind, snippet = block.get("type"), Text(block.get("snippet"))
        if kind == "paragraph" and snippet:
            parsed.append(AiBlock("p", snippet))
        elif kind == "heading" and snippet:
            parsed.append(AiBlock("h", snippet))
        elif kind == "list":
            for item in block.get("list") or []:
                if isinstance(item, dict):
                    text, title = Text(item.get("snippet")), Text(item.get("title"))
                    if text or title:
                        parsed.append(AiBlock("li", text, title))
                    parsed += ParseBlocks(item.get("text_blocks"), depth + 1)
        elif isinstance(block.get("text_blocks"), list):  # e.g. expandable sections
            if Text(block.get("title")):
                parsed.append(AiBlock("h", Text(block["title"])))
            parsed += ParseBlocks(block["text_blocks"], depth + 1)
    return parsed[:MAX_AI_BLOCKS]


def ParseAiOverview(overview: Any) -> AiAnswer | None:
    if not isinstance(overview, dict):
        return None
    blocks = ParseBlocks(overview.get("text_blocks"))
    if not blocks:
        return None
    sources = []
    for reference in overview.get("references") or []:
        if isinstance(reference, dict) and Text(reference.get("title")) and str(reference.get("link", "")).startswith("http"):
            sources.append(AiSource(Text(reference["title"]), reference["link"]))
        if len(sources) >= MAX_SOURCES:
            break
    return AiAnswer(blocks, sources)


class SerpApiClient:
    def __init__(self, apiKey: str, language: str = "en", country: str = "", client: httpx.AsyncClient | None = None):
        self.apiKey, self.language, self.country = apiKey, language, country
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))

    async def Close(self) -> None:
        await self.client.aclose()

    async def _Get(self, params: dict) -> dict:
        try:
            response = await self.client.get(ENDPOINT, params={**params, "api_key": self.apiKey})
        except httpx.TimeoutException as error:
            raise SearchError("The search service took too long to answer.") from error
        except httpx.HTTPError as error:
            LOGGER.warning("Search request failed: %s", type(error).__name__)
            raise SearchError("The search service is not reachable.") from error
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        message = Text(data.get("error"))
        if response.status_code in (401, 403) or "api key" in message.lower() or "invalid key" in message.lower():
            raise SearchServiceError("The search service refused the API key.")
        if response.status_code == 429 or "run out of searches" in message.lower() or "rate limit" in message.lower():
            raise SearchServiceError("The search quota is used up or the service is rate limiting me.")
        if response.status_code >= 400 or (message and "hasn't returned any results" not in message.lower()):
            LOGGER.warning("Search service answered HTTP %s: %s", response.status_code, message.replace(self.apiKey, "***")[:200])
            raise SearchError(f"The search service answered with an error (HTTP {response.status_code}).")
        return data

    async def Search(self, query: str) -> SearchResult:
        """Organic results plus the AI Overview if Google showed one. Raises SearchError."""
        query = " ".join(query.split())[:MAX_QUERY_CHARS]
        params = {"engine": "google", "q": query, "hl": self.language}
        if self.country:
            params["gl"] = self.country
        try:
            data = await self._Get(params)
            result = SearchResult(query, ParseOrganic(data), ParseAiOverview(data.get("ai_overview")))
            overview = data.get("ai_overview")
            if result.ai is None and isinstance(overview, dict) and overview.get("page_token"):
                result.ai = await self._SecondRequest(str(overview["page_token"]))
            return result
        except SearchError:
            raise
        except Exception as error:
            LOGGER.exception("Unexpected failure in the search client")
            raise UnexpectedSearchError("Sorry, something went wrong while searching.") from error

    async def _SecondRequest(self, pageToken: str) -> AiAnswer | None:
        """Some AI Overviews are fetched separately (the token lives about a minute). Failing here only loses the answer."""
        try:
            data = await self._Get({"engine": "google_ai_overview", "page_token": pageToken})
        except SearchError as error:
            LOGGER.info("AI overview request failed: %s", error)
            return None
        return ParseAiOverview(data.get("ai_overview") if isinstance(data.get("ai_overview"), dict) else data)
