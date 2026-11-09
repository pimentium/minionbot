import pytest

from minionbot.config import DEFAULT_USERS_FILE, ConfigError, LoadConfig


def test_load_config_defaults():
    config = LoadConfig({"BOT_TOKEN": "tok", "INITIAL_USER": "1"})
    assert (config.botToken, config.initialUser, config.usersFile) == ("tok", "1", DEFAULT_USERS_FILE)


def test_load_config_override():
    assert LoadConfig({"BOT_TOKEN": "t", "INITIAL_USER": "@abcde", "USERS_FILE": "x.json"}).usersFile == "x.json"


@pytest.mark.parametrize("env", [{}, {"BOT_TOKEN": "t"}, {"INITIAL_USER": "1"}, {"BOT_TOKEN": " ", "INITIAL_USER": "1"}])
def test_load_config_missing(env):
    with pytest.raises(ConfigError):
        LoadConfig(env)


def test_explicit_arguments_override_env():
    env = {"BOT_TOKEN": "envtok", "INITIAL_USER": "1", "USERS_FILE": "env.json"}
    config = LoadConfig(env, botToken="argtok", initialUser="@abcde", usersFile="arg.json")
    assert (config.botToken, config.initialUser, config.usersFile) == ("argtok", "@abcde", "arg.json")


def test_arguments_alone_are_enough():
    assert LoadConfig({}, botToken="t", initialUser="5").initialUser == "5"


def test_accounts_from_env_and_cli_override():
    from minionbot.social.models import SocialAccount

    env = {"BOT_TOKEN": "t", "INITIAL_USER": "1", "INSTAGRAM_SESSION_ID": "envsession", "TWITTER_COOKIES_FILE": "/c.txt"}
    config = LoadConfig(env, accountOverrides={"instagram": SocialAccount(sessionId="clisession"), "twitter": SocialAccount(authToken="tok")})
    assert config.accounts["instagram"].sessionId == "clisession"
    assert config.accounts["twitter"].cookiesFile == "/c.txt" and config.accounts["twitter"].authToken == "tok"
    assert "facebook" not in config.accounts


def test_twitter_auth_token_from_env():
    env = {"BOT_TOKEN": "t", "INITIAL_USER": "1", "TWITTER_AUTH_TOKEN": " abc "}
    assert LoadConfig(env).accounts["twitter"].authToken == "abc"


def test_username_password_settings_are_gone():
    env = {"BOT_TOKEN": "t", "INITIAL_USER": "1", "INSTAGRAM_USERNAME": "u", "INSTAGRAM_PASSWORD": "p"}
    assert LoadConfig(env).accounts == {}
