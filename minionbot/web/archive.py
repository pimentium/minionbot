"""Fallback for pages that only serve a teaser or refuse the bot: look for an archived copy.

This is used only in those cases, and it sends the page address to the archive service. Only the Internet Archive is
used: archive.ph drops connections from anything but a browser, so it cannot work for a script.
"""

from __future__ import annotations

import logging
from typing import Protocol
from urllib.parse import quote

import httpx

LOGGER = logging.getLogger(__name__)


class ArchiveProvider(Protocol):
    name: str

    async def Find(self, url: str) -> str | None: ...


class Wayback:
    """Internet Archive: only has pages somebody archived, but with a documented JSON lookup."""

    name = "web.archive.org"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    async def Find(self, url: str) -> str | None:
        try:
            response = await self.client.get(f"https://archive.org/wayback/available?url={quote(url, safe='')}")
            snapshot = response.json()["archived_snapshots"]["closest"]
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        address = snapshot.get("url", "") if snapshot.get("available") else ""
        return address.replace("http://", "https://", 1) if address else None
