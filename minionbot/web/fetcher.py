"""Safe download of web pages: public hosts only, limited redirects, size, time and content type."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from trafilatura.utils import decode_file

from minionbot.web.errors import HttpStatusError, UnsafeUrlError, WebError
from minionbot.web.safety import ResolveHost, Resolver, AssertPublicUrl, IsPublicAddress

LOGGER = logging.getLogger(__name__)

MAX_BYTES = 4_000_000
MAX_REDIRECTS = 5
ERROR_SAMPLE_BYTES = 16_384
BOT_PROTECTION_SERVERS = ("datadome", "cloudflare", "akamai", "perimeterx", "imperva", "incapsula", "sucuri")
BOT_PROTECTION_MARKERS = (
    "datadome", "captcha-delivery", "cf-chl", "just a moment", "attention required", "px-captcha", "perimeterx",
    "are you a robot", "unusual traffic", "verify you are human", "access denied", "bot detection",
)
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
# Many sites refuse unknown clients; these headers look like a normal browser visit.
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",  # not zstd: httpx can't decode it
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


@dataclass(frozen=True)
class FetchedPage:
    url: str  # the address that was asked for
    finalUrl: str  # after redirects
    html: str


def LooksLikeBotProtection(response: httpx.Response, sample: bytes) -> bool:
    """A refusal page of a bot-protection service (DataDome, Cloudflare, ...), judged by headers and page text."""
    headers = " ".join(f"{key}:{value}" for key, value in response.headers.items()).lower()
    refusal = response.status_code in (401, 403, 429, 503)
    server = response.headers.get("server", "").lower()
    if refusal and (any(name in server for name in BOT_PROTECTION_SERVERS) or "datadome" in headers or "cf-mitigated" in headers):
        return True
    return refusal and any(marker in sample.decode("utf-8", "ignore").lower() for marker in BOT_PROTECTION_MARKERS)


def PeerAddress(response: httpx.Response) -> str | None:
    """The address we actually connected to (None if the transport doesn't tell, e.g. in tests)."""
    stream = response.extensions.get("network_stream")
    info = stream.get_extra_info("server_addr") if stream is not None else None
    return info[0] if info else None


class PageFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None, resolver: Resolver = ResolveHost):
        self.client = client or httpx.AsyncClient(headers=PAGE_HEADERS, follow_redirects=False, timeout=httpx.Timeout(20.0, connect=8.0))
        self.resolver = resolver

    async def Close(self) -> None:
        await self.client.aclose()

    async def Fetch(self, url: str) -> FetchedPage:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await AssertPublicUrl(current, self.resolver)  # every hop, not just the first address
            try:
                async with self.client.stream("GET", current) as response:
                    peer = PeerAddress(response)
                    if peer is not None and not IsPublicAddress(peer):
                        raise UnsafeUrlError("I can't open addresses on private or local networks.")
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebError("The site sent a broken redirect.")
                        current = urljoin(current, location)
                        continue
                    return FetchedPage(url, current, await self._ReadBody(response))
            except httpx.TimeoutException as error:
                raise WebError("The site took too long to answer.") from error
            except httpx.HTTPError as error:
                LOGGER.warning("Page fetch failed for %s: %s", url, type(error).__name__)
                raise WebError("I couldn't open that page.") from error
        raise WebError("Too many redirects.")

    async def _ReadBody(self, response: httpx.Response) -> str:
        if response.status_code >= 400:
            sample = b""
            async for chunk in response.aiter_bytes():
                sample += chunk
                if len(sample) >= ERROR_SAMPLE_BYTES:
                    break
            raise HttpStatusError(response.status_code, LooksLikeBotProtection(response, sample))
        contentType = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if contentType and contentType not in ALLOWED_TYPES:
            raise WebError(f"That is not a web page ({contentType}).")
        if int(response.headers.get("content-length") or 0) > MAX_BYTES:
            raise WebError("That page is too large.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise WebError("That page is too large.")
        return decode_file(bytes(body)) or ""
