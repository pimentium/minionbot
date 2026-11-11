"""Publish an article to telegra.ph so Telegram shows it with Instant View (one-tap reader).

Pages are public to anyone who has the link. The access token is created on first use and kept in a 0600
file next to the users file (or given explicitly).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

from minionbot.web.errors import WebError
from minionbot.web.reader import Node, TrimNodes

LOGGER = logging.getLogger(__name__)

API = "https://api.telegra.ph"
CONTENT_LIMIT_BYTES = 58_000  # Telegraph rejects pages over 64 KB
SHORT_NAME = "minionbot"
READABLE_CHECKS = 4  # tries to see the new page before it is announced
READABLE_DELAY = 0.5


class TelegraphClient:
    def __init__(self, tokenFile: str | Path, token: str = "", client: httpx.AsyncClient | None = None):
        self.tokenFile = Path(tokenFile)
        self.token = token
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def Close(self) -> None:
        await self.client.aclose()

    async def _Call(self, method: str, payload: dict) -> dict:
        try:
            response = await self.client.post(f"{API}/{method}", json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise WebError("Telegraph is not reachable.") from error
        if not data.get("ok"):
            raise WebError(f"Telegraph refused the page ({data.get('error', 'unknown error')}).")
        return data["result"]

    async def Token(self) -> str:
        if self.token:
            return self.token
        try:
            self.token = json.loads(self.tokenFile.read_text(encoding="utf-8"))["access_token"]
            return self.token
        except (OSError, ValueError, KeyError):
            pass
        account = await self._Call("createAccount", {"short_name": SHORT_NAME, "author_name": SHORT_NAME})
        self.token = account["access_token"]
        self.tokenFile.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.tokenFile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"access_token": self.token}, handle)
        return self.token

    async def CreatePage(self, title: str, author: str, authorUrl: str, nodes: list[Node], sourceUrl: str) -> str:
        """Create the page and return its URL. Over-long articles are cut and end with a link to the source."""
        fitted, trimmed = TrimNodes(nodes, CONTENT_LIMIT_BYTES)
        if trimmed:
            fitted.append({"tag": "p", "children": [{"tag": "a", "attrs": {"href": sourceUrl}, "children": ["Continue reading at the source"]}]})
        page = await self._Call(
            "createPage",
            {
                "access_token": await self.Token(),
                "title": title[:256] or "Untitled",
                "author_name": author[:128],
                "author_url": authorUrl[:512] if authorUrl.startswith("http") else "",
                "content": fitted,
                "return_content": False,
            },
        )
        await self._WaitUntilReadable(page["url"])
        return page["url"]

    async def _WaitUntilReadable(self, url: str) -> None:
        """Telegram builds the Instant View card when the message arrives; make sure the page is already served.

        Never raises: a slow page only costs the card, not the reply.
        """
        for attempt in range(READABLE_CHECKS):
            try:
                if (await self.client.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if attempt + 1 < READABLE_CHECKS:
                await asyncio.sleep(READABLE_DELAY)
        LOGGER.warning("The new Telegraph page %s was not readable yet; the preview card may be missing", url)
