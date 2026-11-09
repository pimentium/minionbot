"""Data types shared by the social downloaders."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PHOTO = "photo"
VIDEO = "video"

NETWORK_LABELS = {
    "instagram": "Instagram",
    "facebook": "Facebook",
    "twitter": "Twitter/X",
    "threads": "Threads",
}


class DownloadError(Exception):
    """A user-facing reason why a post could not be downloaded."""


class UnexpectedDownloadError(DownloadError):
    """A download failed because of a bug or unforeseen error (the owner should be told); see __cause__."""


@dataclass(frozen=True)
class Link:
    network: str
    url: str


@dataclass(frozen=True)
class RemoteMedia:
    url: str
    kind: str  # PHOTO or VIDEO


@dataclass
class RemotePost:
    """A post as described by a network: text and media URLs, nothing downloaded yet."""

    network: str
    url: str
    author: str
    text: str
    media: list[RemoteMedia] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LocalMedia:
    path: Path
    kind: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None


@dataclass
class Post:
    network: str
    url: str
    author: str
    text: str
    media: list[LocalMedia] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SocialAccount:
    """Optional browser-session credentials for one network. Never log or persist these."""

    cookiesFile: str = ""  # Netscape cookies file exported from a logged-in browser
    authToken: str = ""  # Twitter/X "auth_token" cookie value
    sessionId: str = ""  # Instagram "sessionid" cookie value

    def IsSet(self) -> bool:
        return bool(self.cookiesFile or self.authToken or self.sessionId)

    def HasSession(self) -> bool:
        return self.IsSet()
