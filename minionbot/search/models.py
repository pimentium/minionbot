"""Types for Google search results (via a SERP API)."""

from __future__ import annotations

from dataclasses import dataclass, field


class SearchError(Exception):
    """A user-facing reason why the search failed."""


class SearchServiceError(SearchError):
    """The search service itself has a problem the owner must fix (bad key, quota used up)."""


class UnexpectedSearchError(SearchError):
    """A bug or unforeseen failure; the owner should be told. See __cause__."""


@dataclass(frozen=True)
class OrganicResult:
    title: str
    link: str
    snippet: str = ""
    displayedLink: str = ""


@dataclass(frozen=True)
class AiBlock:
    kind: str  # "p" paragraph, "h" heading, "li" list item
    text: str
    title: str = ""  # list items may carry a bold lead-in


@dataclass(frozen=True)
class AiSource:
    title: str
    link: str


@dataclass
class AiAnswer:
    blocks: list[AiBlock] = field(default_factory=list)
    sources: list[AiSource] = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    organic: list[OrganicResult] = field(default_factory=list)
    ai: AiAnswer | None = None
