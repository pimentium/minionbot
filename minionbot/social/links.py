"""Find links to supported social networks inside message text."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
# Facebook links whose path alone identifies the post: any query (__cft__, __tn__, fbclid, ...) is noise.
FACEBOOK_PATH = re.compile(
    r"^/(?:[^/]+/(?:posts|videos|photos)/|reel/|share/|groups/[^/]+/(?:posts|permalink)/)",
    re.IGNORECASE,
)
# Older Facebook link forms identified by query parameters: (path, parameters kept, all required).
FACEBOOK_QUERY_FORMS = [
    (re.compile(r"^/watch/?$", re.IGNORECASE), ("v",)),
    (re.compile(r"^/photo(?:\.php)?/?$", re.IGNORECASE), ("fbid",)),
    (re.compile(r"^/(?:permalink|story)\.php$", re.IGNORECASE), ("story_fbid", "id")),
]
HOST_PREFIXES = re.compile(r"^(?:www\.|m\.|mobile\.|web\.|mbasic\.)")


def Clean(host: str, path: str, query: str = "") -> str:
    return urlunsplit(("https", host, path, query, ""))


def FacebookLink(host: str, path: str, query: str) -> Link | None:
    if host == "fb.watch":
        return Link("facebook", Clean(host, path)) if path.strip("/") else None
    for pattern, keep in FACEBOOK_QUERY_FORMS:
        if pattern.match(path):
            values = dict(parse_qsl(query))
            if any(not values.get(name) for name in keep):
                return None
            return Link("facebook", Clean("www.facebook.com", path, urlencode([(name, values[name]) for name in keep])))
    if FACEBOOK_PATH.match(path):
        return Link("facebook", Clean("www.facebook.com", path))
    return None


def Classify(url: str) -> Link | None:
    """Return a normalized, tracking-free Link if the URL points to a post on a supported network.

    Only what identifies the post is kept (the path, or for old Facebook forms a few named parameters);
    queries, fragments and mobile/regional host prefixes are dropped.
    """
    url = url.rstrip(TRAILING_PUNCTUATION)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host, path = (parts.hostname or "").lower(), parts.path
    if host in INSTAGRAM_HOSTS and (match := INSTAGRAM_PATH.match(path)):
        return Link("instagram", Clean("www.instagram.com", match.group(0).rstrip("/") + "/"))
    if host in TWITTER_HOSTS and (match := TWITTER_PATH.match(path)):
        return Link("twitter", Clean(HOST_PREFIXES.sub("", host), match.group(0)))
    if host in THREADS_HOSTS and (match := THREADS_PATH.match(path)):
        return Link("threads", Clean("www." + HOST_PREFIXES.sub("", host), match.group(0)))
    if host in FACEBOOK_SHORT_HOSTS or host in FACEBOOK_HOSTS:
        return FacebookLink(host, path, parts.query)
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
