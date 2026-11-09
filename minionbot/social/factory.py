"""Build the per-network downloaders from configuration."""

from __future__ import annotations

from minionbot.social.gallerydl import GalleryDlDownloader
from minionbot.social.instagram import InstagramDownloader
from minionbot.social.models import SocialAccount
from minionbot.social.threads import ThreadsDownloader

NETWORKS = ("instagram", "facebook", "twitter", "threads")


def BuildDownloaders(accounts: dict[str, SocialAccount]) -> dict:
    downloaders: dict = {
        "twitter": GalleryDlDownloader("twitter", accounts.get("twitter")),
        "facebook": GalleryDlDownloader("facebook", accounts.get("facebook")),
        "threads": ThreadsDownloader(),
    }
    instagram = accounts.get("instagram")
    if instagram and instagram.sessionId:
        downloaders["instagram"] = InstagramDownloader(instagram.sessionId)
    else:
        downloaders["instagram"] = GalleryDlDownloader("instagram", instagram)
    return downloaders
