"""Entry point: python -m minionbot.main"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Sequence

from dotenv import load_dotenv
from telegram.error import InvalidToken

from minionbot.bot import BuildApplication, ConfigureLogging, RedactSecrets
from minionbot.builtin import BuildRegistry
from minionbot.config import LoadConfig
from minionbot.social.factory import BuildDownloaders
from minionbot.social.fetch import MediaFetcher
from minionbot.social.instagram import InstagramDownloader
from minionbot.social.models import DownloadError, SocialAccount
from minionbot.social.service import SocialService
from minionbot.supervisor import RunForever
from minionbot.users import UserStore


def ParseArgs(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="minionbot", description="Personal Telegram bot.")
    parser.add_argument("--token", help="bot token from @BotFather (overrides BOT_TOKEN)")
    parser.add_argument("--initial-user", help="numeric Telegram ID or @username of the first user (overrides INITIAL_USER)")
    parser.add_argument("--users-file", help="path to the users JSON file (overrides USERS_FILE)")
    for network in ("instagram", "twitter", "facebook"):
        parser.add_argument(f"--{network}-cookies", help=f"{network} cookies file, Netscape format (overrides {network.upper()}_COOKIES_FILE)")
    parser.add_argument("--twitter-auth-token", help="value of the auth_token cookie from a logged-in x.com browser session (overrides TWITTER_AUTH_TOKEN)")
    parser.add_argument("--instagram-session-id", help="value of the sessionid cookie from a logged-in instagram.com browser session (overrides INSTAGRAM_SESSION_ID)")
    return parser.parse_args(argv)


def AccountOverrides(args: argparse.Namespace) -> dict[str, SocialAccount]:
    return {
        "instagram": SocialAccount(cookiesFile=args.instagram_cookies or "", sessionId=args.instagram_session_id or ""),
        "twitter": SocialAccount(cookiesFile=args.twitter_cookies or "", authToken=args.twitter_auth_token or ""),
        "facebook": SocialAccount(cookiesFile=args.facebook_cookies or ""),
    }


def IsFatal(error: BaseException) -> bool:
    """Errors that restarting cannot fix."""
    return isinstance(error, InvalidToken)


def Main(argv: Sequence[str] | None = None) -> None:
    args = ParseArgs(argv)
    load_dotenv()
    ConfigureLogging()
    config = LoadConfig(botToken=args.token, initialUser=args.initial_user, usersFile=args.users_file, accountOverrides=AccountOverrides(args))
    secrets = [config.botToken] + [v for a in config.accounts.values() for v in (a.sessionId, a.authToken)]
    RedactSecrets(secrets)
    store = UserStore(config.usersFile)
    owner = store.EnsureInitialUser(config.initialUser)
    logging.getLogger(__name__).info("Starting with %d user(s); initial user: %s", len(store.List()), owner.name)

    def RunOnce(restartCount: int, lastError: str) -> None:
        # Everything that holds an event loop (HTTP clients, the application) is rebuilt on every restart.
        downloaders = BuildDownloaders(config.accounts)
        instagram = downloaders["instagram"]
        if isinstance(instagram, InstagramDownloader):
            try:
                instagram.Login()
            except DownloadError as error:
                logging.getLogger(__name__).warning("Instagram login at startup failed; will retry on first link: %s", error)
        service = SocialService(downloaders, MediaFetcher())
        restartInfo = f"restart #{restartCount} after: {lastError}" if restartCount else ""
        application = BuildApplication(config, store, BuildRegistry(), service, restartInfo)
        asyncio.set_event_loop(asyncio.new_event_loop())
        application.run_polling()

    def Scrub(text: str) -> str:
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    try:
        RunForever(RunOnce, isFatal=IsFatal, scrub=Scrub)
    except Exception as error:
        if not IsFatal(error):
            raise
        # Already logged (redacted). Exit without Python's own traceback, which would print the bot token.
        raise SystemExit(1) from None


if __name__ == "__main__":
    Main()
