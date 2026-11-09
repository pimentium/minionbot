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

from minionbot.social.models import PHOTO, VIDEO, DownloadError, RemoteMedia, RemotePost

LOGGER = logging.getLogger(__name__)

# Threads serves a stripped page without any post data to unknown browsers (checked: the Chrome-style headers
# used for media downloads get ~270 KB and no post; these get the full page). zstd is left out because httpx
# can't decode it.
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

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


def SameAuthorPostsInThreadItems(blobs: list, code: str) -> list[dict]:
    """Older page layout: a thread_items list containing the post lists the author's parts."""
    for blob in blobs:
        for node in Walk(blob):
            items = node.get("thread_items")
            if isinstance(items, list):
                posts = [i["post"] for i in items if isinstance(i, dict) and isinstance(i.get("post"), dict)]
                target = next((p for p in posts if p.get("code") == code), None)
                if target:
                    username = (target.get("user") or {}).get("username")
                    return [p for p in posts if (p.get("user") or {}).get("username") == username]
    return []


def SelfThreadNodes(post: dict) -> list[dict]:
    """The author's later parts, from text_post_app_info.self_thread, in the page's own order (post_ids)."""
    info = post.get("text_post_app_info") or {}
    thread = info.get("self_thread") or {}
    nodes = [edge["node"] for edge in (thread.get("posts") or {}).get("edges") or [] if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]
    order = [
        (edge.get("node") or {}).get("pk")
        for edge in (thread.get("post_ids") or {}).get("edges") or []
        if isinstance(edge, dict)
    ]
    if order:
        nodes.sort(key=lambda node: order.index(node.get("pk")) if node.get("pk") in order else len(order))
    return nodes


def AuthorLabel(user: dict) -> str:
    username, fullName = user.get("username") or "", user.get("full_name") or ""
    if fullName and username:
        return f"{fullName} (@{username})"
    return f"@{username}" if username else fullName


def ParseThreadsPage(url: str, html: str) -> RemotePost:
    code = urlsplit(url).path.rstrip("/").split("/")[-1]
    blobs = ExtractJsonBlobs(html)
    copies = [node for blob in blobs for node in Walk(blob) if node.get("code") == code and isinstance(node.get("user"), dict)]
    parts = SameAuthorPostsInThreadItems(blobs, code)
    if not parts and copies:
        parts = [copies[0]]
    if not parts:
        raise DownloadError("Could not read this Threads post (it may need a login, or Threads changed its page).")
    known = {part.get("code") for part in parts}
    postId = parts[0].get("id")
    # The rest of the thread hangs off a partial copy of the post ({id, text_post_app_info}, no code), so match on id.
    owners = [node for blob in blobs for node in Walk(blob) if postId and node.get("id") == postId]
    for owner in owners:
        for node in SelfThreadNodes(owner):
            if node.get("code") not in known and (node.get("user") or {}).get("username") == parts[0]["user"].get("username"):
                parts.append(node)
                known.add(node.get("code"))
    texts = [(part.get("caption") or {}).get("text") or "" for part in parts]
    media: list[RemoteMedia] = []
    for part in parts:
        media += PostMedia(part)
    return RemotePost("threads", url, AuthorLabel(parts[0]["user"]), "\n\n".join(t for t in texts if t), media)


class ThreadsDownloader:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(headers=PAGE_HEADERS, follow_redirects=True, timeout=30)

    def Fetch(self, url: str) -> RemotePost:
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            LOGGER.warning("Threads fetch failed for %s: %s", url, type(error).__name__)
            raise DownloadError("Could not open this Threads post.") from error
        return ParseThreadsPage(url, response.text)
