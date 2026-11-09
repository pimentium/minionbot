"""Facebook posts: gallery-dl supplies the media, the post page supplies who posted and what they wrote.

gallery-dl follows a *shared* post to the original and returns only the original's photos, text and author.
The page itself carries the sharer's own story (author + comment) with the original attached, so we read that
too and combine both. Everything on the page side is best effort: failures fall back to gallery-dl's data.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar

import httpx

from minionbot.social.gallerydl import GalleryDlDownloader
from minionbot.social.models import DownloadError, RemotePost

LOGGER = logging.getLogger(__name__)

SCRIPT_PATTERN = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.DOTALL)
# Facebook rejects unknown browsers (HTTP 400). zstd is not offered because httpx can't decode it.
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.facebook.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}
SHARED_HEADER = "↪ Shared post by {author}:"


@dataclass
class FacebookStory:
    author: str
    text: str
    shared: FacebookStory | None = None


def Walk(node):
    """Yield every dict in a nested JSON structure, in document order."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from Walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from Walk(value)


def IsStory(node: dict) -> bool:
    actors = node.get("actors")
    return isinstance(actors, list) and bool(actors) and "message" in node and "post_id" in node


def BuildStory(node: dict, depth: int = 0) -> FacebookStory:
    message = node.get("message")
    text = message.get("text", "") if isinstance(message, dict) else ""
    actor = node["actors"][0] if isinstance(node.get("actors"), list) and node["actors"] else {}
    attached = node.get("attached_story")
    shared = BuildStory(attached, depth + 1) if isinstance(attached, dict) and IsStory(attached) and depth < 3 else None
    return FacebookStory(str(actor.get("name") or ""), text or "", shared)


def ParseFacebookPage(html: str) -> FacebookStory | None:
    """The first story on the page: the post itself, with a shared post attached if there is one."""
    for raw in SCRIPT_PATTERN.findall(html):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in Walk(data):
            if IsStory(node):
                return BuildStory(node)
    return None


class FacebookPageReader:
    def __init__(self, cookiesFile: str = "", client: httpx.Client | None = None):
        cookies = None
        if cookiesFile:
            try:
                cookies = MozillaCookieJar(cookiesFile)
                cookies.load(ignore_discard=True, ignore_expires=True)
            except (OSError, ValueError) as error:
                LOGGER.warning("Could not read the Facebook cookies file (%s)", type(error).__name__)
                cookies = None
        self.client = client or httpx.Client(headers=PAGE_HEADERS, cookies=cookies, follow_redirects=True, timeout=30)

    def Read(self, url: str) -> FacebookStory | None:
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            LOGGER.warning("Facebook page fetch failed for %s: %s", url, type(error).__name__)
            return None
        return ParseFacebookPage(response.text)


def Combine(post: RemotePost, story: FacebookStory | None) -> RemotePost:
    """Merge gallery-dl's view of the post (media, original author/text) with the page's view (sharer)."""
    if story is None:
        return post
    if story.shared is None and not story.text and not story.author:
        return post
    if story.shared is None:  # an ordinary post: prefer the page's author/text, keep gallery-dl's as backup
        post.author = story.author or post.author
        post.text = story.text or post.text
        return post
    originalAuthor = story.shared.author or post.author
    originalText = story.shared.text or post.text
    parts = [story.text] if story.text else []
    if originalText or originalAuthor:
        parts.append("\n".join(filter(None, [SHARED_HEADER.format(author=originalAuthor or "someone"), originalText])))
    post.author = story.author or post.author
    post.text = "\n\n".join(parts)
    return post


class FacebookDownloader:
    def __init__(self, galleryDl: GalleryDlDownloader, reader: FacebookPageReader):
        self.galleryDl = galleryDl
        self.reader = reader

    def Fetch(self, url: str) -> RemotePost:
        story = self.reader.Read(url)
        try:
            post = self.galleryDl.Fetch(url)
        except DownloadError:
            if story is None or not (story.text or (story.shared and story.shared.text)):
                raise
            post = RemotePost("facebook", url, "", "")  # a post without media: text only
        return Combine(post, story)
