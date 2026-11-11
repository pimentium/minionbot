"""Find ordinary web page links in messages (everything that is not a social network link)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from minionbot.social.links import (
    FACEBOOK_HOSTS,
    FACEBOOK_SHORT_HOSTS,
    INSTAGRAM_HOSTS,
    THREADS_HOSTS,
    TRAILING_PUNCTUATION,
    TWITTER_HOSTS,
    URL_PATTERN,
)

# Social networks have their own pipeline (/social); their profile pages, login walls etc. are no articles.
EXCLUDED_HOSTS = INSTAGRAM_HOSTS | TWITTER_HOSTS | THREADS_HOSTS | FACEBOOK_HOSTS | FACEBOOK_SHORT_HOSTS | {"t.me", "telegram.me"}
# Generic pages cannot be allow-listed, so only well-known tracking parameters are dropped.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga", "ref_src", "spm",
    "vero_id", "wickedid", "oly_enc_id", "oly_anon_id", "s_cid", "cmpid", "mkt_tok",
}
TRACKING_PREFIXES = ("utm_",)


def CleanWebUrl(url: str) -> str | None:
    """Normalise an http(s) URL: no fragment, no well-known tracking parameters. None if it isn't one."""
    url = url.rstrip(TRAILING_PUNCTUATION)
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or host in EXCLUDED_HOSTS:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", urlencode(query), ""))


def FindWebUrls(text: str, extraUrls: list[str] | None = None) -> list[str]:
    """Web page links in text plus explicit URLs (hidden text links), in order, without duplicates."""
    urls: list[str] = []
    for candidate in URL_PATTERN.findall(text or "") + list(extraUrls or []):
        cleaned = CleanWebUrl(candidate)
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls
