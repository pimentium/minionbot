"""gallery-dl based downloader (Twitter/X, Facebook, and Instagram when no instagrapi login is set).

gallery-dl runs as a subprocess with ``-j`` (dump metadata as JSON, nothing downloaded); the media
files are then downloaded by MediaFetcher. Credentials go through a temporary 0600 config file, not
through the command line.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from minionbot.social.models import PHOTO, VIDEO, DownloadError, RemoteMedia, RemotePost, SocialAccount

LOGGER = logging.getLogger(__name__)

URL_MESSAGE = 3
PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "gif"}  # gallery-dl serves Twitter GIFs as mp4
# "title" is deliberately absent: on Facebook it is a generated "Photos from X's post". post_text (Facebook) is the
# whole post, "caption" only a single photo's caption.
TEXT_KEYS = ("post_text", "content", "message", "caption", "description", "text")


def MediaKind(url: str, meta: dict) -> str | None:
    extension = str(meta.get("extension") or url.split("?")[0].rsplit(".", 1)[-1]).lower()
    if extension in VIDEO_EXTENSIONS:
        return VIDEO
    if extension in PHOTO_EXTENSIONS:
        return PHOTO
    if "format=jpg" in url or "format=png" in url or "format=webp" in url:
        return PHOTO
    return None


def FirstText(meta: dict) -> str:
    for key in TEXT_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def AuthorName(meta: dict) -> str:
    author = meta.get("author") or meta.get("user")
    if isinstance(author, dict):
        nick, name = author.get("nick") or author.get("full_name"), author.get("name") or author.get("username")
        if nick and name:
            return f"{nick} (@{name})"
        return f"@{name}" if name else (nick or "")
    username = meta.get("username")
    if isinstance(username, str) and username:
        return username
    return author if isinstance(author, str) else ""


ERROR_MESSAGE = -1
DIRECTORY_MESSAGE = 2


def Messages(output: str) -> list:
    try:
        messages = json.loads(output)
    except ValueError as error:
        raise DownloadError("Unexpected output from the downloader.") from error
    return [m for m in messages if isinstance(m, list) and len(m) >= 2]


def ErrorOf(messages: list) -> str | None:
    """gallery-dl reports failures as [-1, {"error": ..., "message": ...}]."""
    for message in messages:
        if message[0] == ERROR_MESSAGE and isinstance(message[1], dict):
            return f"{message[1].get('error', 'Error')}: {message[1].get('message', '')}"
    return None


def Entries(output: str) -> list[tuple[str, dict]]:
    """(url, metadata) of every file message in `gallery-dl -j` output."""
    return [(m[1], m[2]) for m in Messages(output) if m[0] == URL_MESSAGE and len(m) >= 3]


def TweetEntries(messages: list) -> list[tuple[str, dict]]:
    """Tweet records. Text-only tweets arrive only as a directory message, so those count too."""
    entries = []
    for m in messages:
        if m[0] == DIRECTORY_MESSAGE and isinstance(m[1], dict) and "tweet_id" in m[1]:
            entries.append(("", m[1]))
        elif m[0] == URL_MESSAGE and len(m) >= 3:
            entries.append((m[1], m[2]))
    return entries


def ParseGeneric(network: str, url: str, entries: list[tuple[str, dict]]) -> RemotePost:
    post = RemotePost(network, url, "", "")
    for fileUrl, meta in entries:
        post.author = post.author or AuthorName(meta)
        post.text = post.text or FirstText(meta)
        if not fileUrl.startswith("http"):
            continue  # "text:" pseudo-files, "ytdl:" fallbacks
        kind = MediaKind(fileUrl, meta)
        if kind and all(m.url != fileUrl for m in post.media):
            post.media.append(RemoteMedia(fileUrl, kind))
    return post


def MergeTwitterThread(url: str, entries: list[tuple[str, dict]]) -> RemotePost:
    """Combine the author's self-reply chain around the linked tweet into one post."""
    tweets: dict[int, dict] = {}
    for fileUrl, meta in entries:
        if "tweet_id" not in meta or meta.get("retweet_id"):
            continue
        tweet = tweets.setdefault(
            int(meta["tweet_id"]),
            {"meta": meta, "text": FirstText(meta), "media": []},
        )
        kind = MediaKind(fileUrl, meta) if fileUrl.startswith("http") else None
        if kind and all(m.url != fileUrl for m in tweet["media"]):
            tweet["media"].append(RemoteMedia(fileUrl, kind))
    if not tweets:
        raise DownloadError("Could not read this tweet (it may be deleted, protected, or need a login).")
    lastSegment = url.split("?")[0].rstrip("/").split("/")[-1]
    linkedId = int(lastSegment) if lastSegment.isdigit() else None
    linked = tweets.get(linkedId) or tweets[min(tweets)]
    authorId = str((linked["meta"].get("author") or {}).get("id", ""))
    rootId = int(linked["meta"].get("conversation_id") or 0)
    kept: list[int] = []
    for tweetId in sorted(tweets):
        meta = tweets[tweetId]["meta"]
        if str((meta.get("author") or {}).get("id", "")) != authorId:
            continue
        replyId = int(meta.get("reply_id") or 0)
        if tweetId == rootId or replyId in kept or tweets[tweetId] is linked:
            kept.append(tweetId)
    post = RemotePost("twitter", url, AuthorName(linked["meta"]), "")
    post.text = "\n\n".join(tweets[i]["text"] for i in kept if tweets[i]["text"])
    for tweetId in kept:
        post.media += tweets[tweetId]["media"]
    return post


def BuildConfig(network: str, account: SocialAccount | None, conversations: bool = False) -> dict:
    options: dict = {}
    if network == "twitter":
        options.update({"text-tweets": True})
        if conversations:
            options["conversations"] = True
    if account:
        if account.cookiesFile:
            options["cookies"] = account.cookiesFile
        elif account.authToken:
            options["cookies"] = {"auth_token": account.authToken}
    return {"extractor": {network: options}}


class GalleryDlDownloader:
    def __init__(self, network: str, account: SocialAccount | None = None, timeout: int = 150):
        self.network = network
        self.account = account
        self.timeout = timeout

    def Fetch(self, url: str) -> RemotePost:
        if self.network == "twitter":
            return self._FetchTwitter(url)
        messages = Messages(self._Run(url, self.account))
        post = ParseGeneric(self.network, url, Entries(json.dumps(messages)))
        if not post.media and not post.text:
            LOGGER.warning("gallery-dl found nothing for %s: %s", url, ErrorOf(messages))
            raise DownloadError("Nothing found at this link (it may be private, deleted, or need a login/cookies).")
        return post

    def _FetchTwitter(self, url: str) -> RemotePost:
        """Whole thread when logged in; otherwise (or if that fails) just the linked tweet."""
        if self.account and self.account.HasSession():
            messages = Messages(self._Run(url, self.account, conversations=True))
            error = ErrorOf(messages)
            entries = TweetEntries(messages)
            if entries and not error:
                return MergeTwitterThread(url, entries)
            LOGGER.warning("Twitter thread fetch failed for %s (%s); falling back to the single tweet", url, error)
        messages = Messages(self._Run(url, None, conversations=False))
        entries = TweetEntries(messages)
        if not entries:
            LOGGER.warning("Twitter single-tweet fetch failed for %s: %s", url, ErrorOf(messages))
            hint = "" if self.account and self.account.HasSession() else " The owner can set --twitter-auth-token (see README)."
            raise DownloadError(f"Could not read this tweet (deleted, protected, or it needs a login).{hint}")
        post = MergeTwitterThread(url, entries)
        if self.account and self.account.HasSession():
            post.notes.append("(Only this tweet: the Twitter session did not work, so the rest of the thread was not collected.)")
        return post

    @staticmethod
    def _Scrub(text: str, account: SocialAccount | None) -> str:
        for secret in (account.authToken, account.sessionId) if account else ():
            if secret:
                text = text.replace(secret, "***")
        return text

    def _Run(self, url: str, account: SocialAccount | None, conversations: bool = False) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            configPath = Path(tmp) / "gallery-dl.json"
            fd = os.open(configPath, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(BuildConfig(self.network, account, conversations), handle)
            command = [sys.executable, "-m", "gallery_dl", "--config-ignore", "-c", str(configPath), "-j", url]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired as error:
                raise DownloadError("Timed out while reading this post.") from error
        if result.stderr.strip():
            LOGGER.warning("gallery-dl stderr for %s: %s", url, self._Scrub(result.stderr.strip()[-400:], account))
        if result.returncode != 0 and not result.stdout.strip().startswith("["):
            raise DownloadError("Could not read this post (it may be private, deleted, or need a login).")
        return result.stdout
