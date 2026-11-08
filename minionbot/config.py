"""Configuration from environment variables (optionally loaded from .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_USERS_FILE = "data/users.json"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    botToken: str
    initialUser: str
    usersFile: str


def LoadConfig(
    env: Mapping[str, str] | None = None,
    botToken: str | None = None,
    initialUser: str | None = None,
    usersFile: str | None = None,
) -> Config:
    """Explicit arguments (e.g. from the command line) win over environment variables."""
    env = os.environ if env is None else env
    token = (botToken or env.get("BOT_TOKEN", "")).strip()
    owner = (initialUser or env.get("INITIAL_USER", "")).strip()
    if not token:
        raise ConfigError("Bot token is not set (--token or BOT_TOKEN, see .env.example)")
    if not owner:
        raise ConfigError("Initial user is not set (--initial-user or INITIAL_USER): the bot needs at least one user on start")
    return Config(token, owner, (usersFile or env.get("USERS_FILE", "")).strip() or DEFAULT_USERS_FILE)
