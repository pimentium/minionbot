"""Configuration from environment variables (optionally loaded from .env) and command-line overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from minionbot.social.factory import NETWORKS
from minionbot.social.models import SocialAccount

DEFAULT_USERS_FILE = "data/users.json"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    botToken: str
    initialUser: str
    usersFile: str
    accounts: dict[str, SocialAccount] = field(default_factory=dict)
    telegraphToken: str = ""  # optional; otherwise an account is created on first use
    serpApiKey: str = ""  # SerpApi key for /google; without it /google says it is not configured
    googleLanguage: str = "en"  # hl; Google shows AI overviews for English only
    googleCountry: str = ""  # gl; empty = Google decides


def LoadAccounts(env: Mapping[str, str], overrides: Mapping[str, SocialAccount] | None) -> dict[str, SocialAccount]:
    """Per network: command-line value wins, then <NETWORK>_COOKIES_FILE / _AUTH_TOKEN / _SESSION_ID."""
    accounts: dict[str, SocialAccount] = {}
    for network in NETWORKS:
        prefix = network.upper()
        override = (overrides or {}).get(network, SocialAccount())
        account = SocialAccount(
            cookiesFile=(override.cookiesFile or env.get(f"{prefix}_COOKIES_FILE", "")).strip(),
            authToken=(override.authToken or env.get(f"{prefix}_AUTH_TOKEN", "")).strip(),
            sessionId=(override.sessionId or env.get(f"{prefix}_SESSION_ID", "")).strip(),
        )
        if account.IsSet():
            accounts[network] = account
    return accounts


def LoadConfig(
    env: Mapping[str, str] | None = None,
    botToken: str | None = None,
    initialUser: str | None = None,
    usersFile: str | None = None,
    accountOverrides: Mapping[str, SocialAccount] | None = None,
    telegraphToken: str | None = None,
    serpApiKey: str | None = None,
    googleLanguage: str | None = None,
    googleCountry: str | None = None,
) -> Config:
    """Explicit arguments (e.g. from the command line) win over environment variables."""
    env = os.environ if env is None else env
    token = (botToken or env.get("BOT_TOKEN", "")).strip()
    owner = (initialUser or env.get("INITIAL_USER", "")).strip()
    if not token:
        raise ConfigError("Bot token is not set (--token or BOT_TOKEN)")
    if not owner:
        raise ConfigError("Initial user is not set (--initial-user or INITIAL_USER): the bot needs at least one user on start")
    return Config(
        token,
        owner,
        (usersFile or env.get("USERS_FILE", "")).strip() or DEFAULT_USERS_FILE,
        LoadAccounts(env, accountOverrides),
        (telegraphToken or env.get("TELEGRAPH_TOKEN", "")).strip(),
        (serpApiKey or env.get("SERPAPI_KEY", "")).strip(),
        (googleLanguage or env.get("GOOGLE_LANGUAGE", "")).strip().lower() or "en",
        (googleCountry or env.get("GOOGLE_COUNTRY", "")).strip().lower(),
    )
