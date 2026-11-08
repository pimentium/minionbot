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
