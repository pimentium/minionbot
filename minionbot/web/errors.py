"""Errors of the web page reader."""

from __future__ import annotations


class WebError(Exception):
    """A user-facing reason why a page could not be read."""


class UnsafeUrlError(WebError):
    """The address is not allowed (private network, wrong scheme, ...)."""


class UnexpectedWebError(WebError):
    """A bug or unforeseen failure; the owner should be told. See __cause__."""


# Statuses that mean "the site does not let this client in", as opposed to "the page does not exist".
REFUSAL_STATUSES = {401, 402, 403, 429, 451}


class HttpStatusError(WebError):
    """The site answered with an HTTP error."""

    def __init__(self, status: int, botProtection: bool = False):
        self.status, self.botProtection = status, botProtection
        super().__init__(
            "That site blocks automated access (bot protection), so I can't read it directly."
            if botProtection
            else f"The site answered with an error (HTTP {status})."
        )

    @property
    def refused(self) -> bool:
        """Worth trying an archived copy: access was refused, the page itself is probably fine."""
        return self.botProtection or self.status in REFUSAL_STATUSES
