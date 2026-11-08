"""Entry point: python -m minionbot.main"""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from dotenv import load_dotenv

from minionbot.bot import BuildApplication, ConfigureLogging
from minionbot.builtin import BuildRegistry
from minionbot.config import LoadConfig
from minionbot.users import UserStore


def ParseArgs(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="minionbot", description="Personal Telegram bot.")
    parser.add_argument("--token", help="bot token from @BotFather (overrides BOT_TOKEN)")
    parser.add_argument("--initial-user", help="numeric Telegram ID or @username of the first user (overrides INITIAL_USER)")
    parser.add_argument("--users-file", help="path to the users JSON file (overrides USERS_FILE)")
    return parser.parse_args(argv)


def Main(argv: Sequence[str] | None = None) -> None:
    args = ParseArgs(argv)
    load_dotenv()
    ConfigureLogging()
    config = LoadConfig(botToken=args.token, initialUser=args.initial_user, usersFile=args.users_file)
    store = UserStore(config.usersFile)
    owner = store.EnsureInitialUser(config.initialUser)
    logging.getLogger(__name__).info("Starting with %d user(s); initial user: %s", len(store.List()), owner.name)
    BuildApplication(config, store, BuildRegistry()).run_polling()


if __name__ == "__main__":
    Main()
