"""Find links to supported social networks inside message text."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from minionbot.social.models import Link

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?)]}>…"

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
TWITTER_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com", "x.com", "www.x.com", "mobile.x.com"}
THREADS_HOSTS = {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com", "fb.com", "www.fb.com"}
FACEBOOK_SHORT_HOSTS = {"fb.watch"}

INSTAGRAM_PATH = re.compile(r"^/(?:[\w.]+/)?(?:p|reel|reels|tv)/[\w-]+", re.IGNORECASE)
TWITTER_PATH = re.compile(r"^/(?:i/web|\w+)/status(?:es)?/\d+", re.IGNORECASE)
THREADS_PATH = re.compile(r"^/@[\w.]+/post/[\w-]+", re.IGNORECASE)
FACEBOOK_PATH = re.compile(
    r"^/(?:[^/]+/(?:posts|videos|photos)/|photo(?:\.php|/)|permalink\.php|story\.php|watch|reel/|share/|groups/[^/]+/(?:posts|permalink)/)",
    re.IGNORECASE,
)


def Classify(url: str) -> Link | None:
    """Return a normalized Link if the URL points to a post on a supported network."""
    url = url.rstrip(TRAILING_PUNCTUATION)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host, path = (parts.hostname or "").lower(), parts.path
    if host in INSTAGRAM_HOSTS and INSTAGRAM_PATH.match(path):
        return Link("instagram", urlunsplit(("https", host, path, "", "")))
    if host in TWITTER_HOSTS and TWITTER_PATH.match(path):
        return Link("twitter", urlunsplit(("https", host, path, "", "")))
    if host in THREADS_HOSTS and THREADS_PATH.match(path):
        return Link("threads", urlunsplit(("https", host, path, "", "")))
    if host in FACEBOOK_SHORT_HOSTS and len(path.strip("/")) > 0:
        return Link("facebook", urlunsplit(("https", host, path, parts.query, "")))
    if host in FACEBOOK_HOSTS and (FACEBOOK_PATH.match(path) or ("v=" in parts.query and path.startswith("/watch"))):
        return Link("facebook", urlunsplit(("https", host, path, parts.query, "")))
    return None


def FindLinks(text: str, extraUrls: list[str] | None = None) -> list[Link]:
    """Supported links in text plus explicit URLs (e.g. hidden text-link entities), in order, no duplicates."""
    candidates = URL_PATTERN.findall(text or "") + list(extraUrls or [])
    links: list[Link] = []
    for candidate in candidates:
        link = Classify(candidate)
        if link and link not in links:
            links.append(link)
    return links
