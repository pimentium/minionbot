"""Decide whether (and for which links) the bot must react to an incoming message."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from minionbot.social.links import FindLinks
from minionbot.social.models import Link
from minionbot.web.urls import FindWebUrls

IGNORE = "ignore"
PROCESS = "process"
NO_LINKS = "nolinks"


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
    links, urls = FindLinks(message.text, message.urls), FindWebUrls(message.text, message.urls)
    if not links and not urls and replied is not None and mentioned:
        links, urls = FindLinks(replied.text, replied.urls), FindWebUrls(replied.text, replied.urls)
    return Decision(PROCESS if links or urls else NO_LINKS, mentioned, links, urls)
