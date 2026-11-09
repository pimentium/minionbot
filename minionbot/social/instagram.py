"""Instagram downloader based on instagrapi, logged in with a browser `sessionid` cookie."""

from __future__ import annotations

import logging
import threading

from minionbot.social.models import PHOTO, VIDEO, DownloadError, RemoteMedia, RemotePost

LOGGER = logging.getLogger(__name__)

VIDEO_TYPE, ALBUM_TYPE = 2, 8


class InstagramDownloader:
    def __init__(self, sessionId: str, clientFactory=None):
        self.sessionId = sessionId
        self.clientFactory = clientFactory
        self.client = None
        self.lock = threading.Lock()  # instagrapi's client is not thread-safe

    def _Scrub(self, text: str) -> str:
        return text.replace(self.sessionId, "***") if self.sessionId else text

    def _NewClient(self):
        if self.clientFactory:
            return self.clientFactory()
        from instagrapi import Client

        client = Client()
        client.delay_range = [1, 3]
        return client

    def _Login(self):
        client = self._NewClient()
        try:
            client.login_by_sessionid(self.sessionId)
        except Exception as error:
            LOGGER.warning("Instagram sessionid login failed: %s: %s", type(error).__name__, self._Scrub(str(error))[:300])
            raise DownloadError(f"Instagram session login failed ({type(error).__name__}). The sessionid may have expired.") from error
        return client

    def Login(self) -> None:
        """Log in now, so a bad session shows up at startup."""
        with self.lock:
            if self.client is None:
                self.client = self._Login()

    def Fetch(self, url: str) -> RemotePost:
        with self.lock:
            if self.client is None:
                self.client = self._Login()
            try:
                info = self.client.media_info(self.client.media_pk_from_url(url))
            except Exception as error:
                LOGGER.warning("Instagram fetch failed for %s: %s: %s", url, type(error).__name__, self._Scrub(str(error))[:300])
                raise DownloadError(f"Could not read this Instagram post ({type(error).__name__}).") from error
        return self.ToRemotePost(url, info)

    @staticmethod
    def ToRemotePost(url: str, info) -> RemotePost:
        media: list[RemoteMedia] = []
        items = info.resources if info.media_type == ALBUM_TYPE and info.resources else [info]
        for item in items:
            if item.media_type == VIDEO_TYPE and item.video_url:
                media.append(RemoteMedia(str(item.video_url), VIDEO))
            elif item.thumbnail_url:
                media.append(RemoteMedia(str(item.thumbnail_url), PHOTO))
        username = info.user.username if info.user else ""
        return RemotePost("instagram", url, f"@{username}" if username else "", info.caption_text or "", media)
