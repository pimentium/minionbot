"""Download media files of a RemotePost into a local directory, honoring Telegram size limits."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from minionbot.social.models import PHOTO, VIDEO, LocalMedia, Post, RemotePost
from minionbot.social.videoinfo import ReadMp4Info

LOGGER = logging.getLogger(__name__)

MAX_VIDEO_BYTES = 50 * 1000 * 1000  # Telegram Bot API upload limit
MAX_PHOTO_BYTES = 10 * 1000 * 1000
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
EXTENSIONS = {PHOTO: ".jpg", VIDEO: ".mp4"}


class MediaFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=httpx.Timeout(60.0)
        )

    async def Close(self) -> None:
        await self.client.aclose()

    async def Download(self, remote: RemotePost, directory: Path) -> Post:
        post = Post(remote.network, remote.url, remote.author, remote.text, [], list(remote.notes))
        for index, media in enumerate(remote.media, start=1):
            limit = MAX_VIDEO_BYTES if media.kind == VIDEO else MAX_PHOTO_BYTES
            target = directory / f"{index:02d}{EXTENSIONS[media.kind]}"
            try:
                size = await self._Stream(media.url, target, limit)
            except httpx.HTTPError as error:
                LOGGER.warning("Media download failed (%s): %s", type(error).__name__, media.url)
                post.notes.append(f"Could not download a {media.kind}: {media.url}")
                target.unlink(missing_ok=True)
                continue
            if size is None:
                post.notes.append(f"Skipped a {media.kind} larger than {limit // 1000000} MB (Telegram limit): {media.url}")
                target.unlink(missing_ok=True)
            else:
                info = ReadMp4Info(target) if media.kind == VIDEO else None
                if info:
                    post.media.append(LocalMedia(target, media.kind, info.width, info.height, info.duration))
                else:
                    post.media.append(LocalMedia(target, media.kind))
        return post

    async def _Stream(self, url: str, target: Path, limit: int) -> int | None:
        """Save url to target. Returns bytes written, or None if it exceeds limit."""
        async with self.client.stream("GET", url) as response:
            response.raise_for_status()
            declared = int(response.headers.get("content-length") or 0)
            if declared > limit:
                return None
            written = 0
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    written += len(chunk)
                    if written > limit:
                        return None
                    handle.write(chunk)
            return written
