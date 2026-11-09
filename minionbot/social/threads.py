"""Threads reader.

Neither gallery-dl nor yt-dlp support Threads, so this reads the public post page and extracts the
embedded JSON. It is best effort: Threads changes its page format and may demand a login.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

import httpx

from minionbot.social.fetch import USER_AGENT
from minionbot.social.models import PHOTO, VIDEO, DownloadError, RemoteMedia, RemotePost

LOGGER = logging.getLogger(__name__)

SCRIPT_PATTERN = re.compile(r"<script[^>]*data-sjs[^>]*>(.*?)</script>", re.DOTALL)


def ExtractJsonBlobs(html: str) -> list:
    blobs = []
    for raw in SCRIPT_PATTERN.findall(html):
        try:
            blobs.append(json.loads(raw))
        except ValueError:
            continue
    return blobs


def Walk(node):
    """Yield every dict inside a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from Walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from Walk(value)


def IsPost(node: dict) -> bool:
    return isinstance(node.get("user"), dict) and "code" in node and ("caption" in node or "image_versions2" in node or "video_versions" in node or "carousel_media" in node)


def PostMedia(post: dict) -> list[RemoteMedia]:
    items = post.get("carousel_media") or [post]
    media = []
    for item in items:
        videos = item.get("video_versions") or []
        images = (item.get("image_versions2") or {}).get("candidates") or []
        if videos:
            media.append(RemoteMedia(videos[0]["url"], VIDEO))
        elif images:
            media.append(RemoteMedia(images[0]["url"], PHOTO))
    return media


def ParseThreadsPage(url: str, html: str) -> RemotePost:
    code = urlsplit(url).path.rstrip("/").split("/")[-1]
    blobs = ExtractJsonBlobs(html)
    target = None
    parts: list[dict] = []
    for blob in blobs:
        for node in Walk(blob):
            items = node.get("thread_items")
            if isinstance(items, list):
                posts = [i["post"] for i in items if isinstance(i, dict) and isinstance(i.get("post"), dict)]
                if any(p.get("code") == code for p in posts):
                    target = next(p for p in posts if p.get("code") == code)
                    username = target["user"].get("username")
                    parts = [p for p in posts if p.get("user", {}).get("username") == username]
                    break
        if target:
            break
    if target is None:
        for blob in blobs:
            for node in Walk(blob):
                if IsPost(node) and node.get("code") == code:
                    target, parts = node, [node]
                    break
            if target:
                break
    if target is None:
        raise DownloadError("Could not read this Threads post (it may need a login, or Threads changed its page).")
    username = target["user"].get("username", "")
    texts = [(p.get("caption") or {}).get("text") or "" for p in parts]
    media: list[RemoteMedia] = []
    for part in parts:
        media += PostMedia(part)
    return RemotePost("threads", url, f"@{username}" if username else "", "\n\n".join(t for t in texts if t), media)


class ThreadsDownloader:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}, follow_redirects=True, timeout=30)

    def Fetch(self, url: str) -> RemotePost:
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            LOGGER.warning("Threads fetch failed for %s: %s", url, type(error).__name__)
            raise DownloadError("Could not open this Threads post.") from error
        return ParseThreadsPage(url, response.text)
