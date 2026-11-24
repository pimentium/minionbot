"""Decide whether (and for which links) the bot must react to an incoming message."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from minionbot.social.links import FindLinks, FindUnsupportedSocialUrls
from minionbot.social.models import Link
from minionbot.web.urls import FindWebUrls

IGNORE = "ignore"
PROCESS = "process"
NO_LINKS = "nolinks"
UNSUPPORTED = "unsupported"  # only links to social network pages we cannot download


@dataclass(frozen=True)
class Incoming:
    """The parts of a Telegram message that matter here."""

    text: str = ""
    urls: list[str] = field(default_factory=list)  # URLs from entities (text links)


@dataclass(frozen=True)
class Decision:
    kind: str
    mentioned: bool = False
    links: list[Link] = field(default_factory=list)  # social network posts (/social)
    urls: list[str] = field(default_factory=list)  # all other web pages (/url)
    unsupported: list[str] = field(default_factory=list)  # social network pages that are not downloadable posts


def MentionPattern(botUsername: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@])@{re.escape(botUsername)}\b", re.IGNORECASE)


def IsMentioned(text: str, botUsername: str) -> bool:
    return bool(MentionPattern(botUsername).search(text or ""))


def Decide(isPrivate: bool, botUsername: str, message: Incoming, replied: Incoming | None = None) -> Decision:
    """Rules:
    - Groups: react only if the bot is @mentioned (group commands are ignored).
    - Private chats: react to every message.
    - Links come from the message itself; if it has none and it is a reply that mentions the bot
      (typically just "@bot"), links come from the message it replies to.
    """
    mentioned = IsMentioned(message.text, botUsername)
    if not isPrivate and (not mentioned or message.text.lstrip().startswith("/")):
        return Decision(IGNORE)
    def Find(source: Incoming):
        return FindLinks(source.text, source.urls), FindWebUrls(source.text, source.urls), FindUnsupportedSocialUrls(source.text, source.urls)

    links, urls, unsupported = Find(message)
    if not links and not urls and not unsupported and replied is not None and mentioned:
        links, urls, unsupported = Find(replied)
    kind = PROCESS if links or urls else UNSUPPORTED if unsupported else NO_LINKS
    return Decision(kind, mentioned, links, urls, unsupported)
