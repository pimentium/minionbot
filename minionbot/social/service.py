"""Orchestrates: link -> downloader -> media files."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from minionbot.social.fetch import MediaFetcher
from minionbot.social.models import DownloadError, Link, Post

LOGGER = logging.getLogger(__name__)

NOT_CONFIGURED_TEXT = "I can't download from {network} (not configured)."
FAILED_TEXT = "Sorry, something went wrong while downloading {url}"


class SocialService:
    def __init__(self, downloaders: dict, fetcher: MediaFetcher):
        self.downloaders = downloaders
        self.fetcher = fetcher

    async def Load(self, link: Link, directory: Path) -> Post:
        """Download a post into directory. Raises DownloadError with a user-facing message."""
        downloader = self.downloaders.get(link.network)
        if downloader is None:
            raise DownloadError(NOT_CONFIGURED_TEXT.format(network=link.network))
        try:
            remote = await asyncio.to_thread(downloader.Fetch, link.url)
            return await self.fetcher.Download(remote, directory)
        except DownloadError:
            raise
        except Exception as error:
            LOGGER.exception("Unexpected failure for %s", link.url)
            raise DownloadError(FAILED_TEXT.format(url=link.url)) from error
