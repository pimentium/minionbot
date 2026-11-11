from unittest.mock import MagicMock

import pytest

from minionbot import main


def test_parse_args():
    args = main.ParseArgs(["--token", "t", "--initial-user", "42", "--users-file", "u.json"])
    assert (args.token, args.initial_user, args.users_file) == ("t", "42", "u.json")


def test_parse_args_defaults_to_none():
    args = main.ParseArgs([])
    assert args.token is None and args.initial_user is None


def test_main_uses_cli_args_and_starts_polling(tmp_path, monkeypatch):
    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    application = MagicMock()
    build = MagicMock(return_value=application)
    monkeypatch.setattr(main, "BuildApplication", build)
    usersFile = tmp_path / "u.json"
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())
    main.Main(["--token", "123:FAKE", "--initial-user", "42", "--users-file", str(usersFile), "--twitter-auth-token", "tok", "--instagram-session-id", "sess"])
    config = build.call_args[0][0]
    assert config.botToken == "123:FAKE" and config.initialUser == "42"
    assert '"telegramId": 42' in usersFile.read_text()
    assert config.accounts["twitter"].authToken == "tok" and config.accounts["instagram"].sessionId == "sess"
    assert build.call_args[0][3] is not None
    application.run_polling.assert_called_once()


def test_main_without_credentials_fails(monkeypatch):
    for name in ("BOT_TOKEN", "INITIAL_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    with pytest.raises(Exception, match="not set"):
        main.Main([])


def test_root_main_script_calls_main(monkeypatch):
    import runpy
    from pathlib import Path

    called = MagicMock()
    monkeypatch.setattr(main, "Main", called)
    runpy.run_path(str(Path(main.__file__).parents[1] / "main.py"), run_name="__main__")
    called.assert_called_once_with()


def test_invalid_token_is_fatal_and_not_retried(monkeypatch, tmp_path):
    from telegram.error import InvalidToken

    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())
    application = MagicMock()
    application.run_polling.side_effect = InvalidToken("rejected")
    build = MagicMock(return_value=application)
    monkeypatch.setattr(main, "BuildApplication", build)
    with pytest.raises(SystemExit) as exit_:
        main.Main(["--token", "1:T", "--initial-user", "42", "--users-file", str(tmp_path / "u.json")])
    assert exit_.value.code == 1 and build.call_count == 1


def test_crash_restarts_with_fresh_application_and_reason(monkeypatch, tmp_path):
    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())
    monkeypatch.setattr("minionbot.supervisor.time.sleep", lambda seconds: None)
    crashing, healthy = MagicMock(), MagicMock()
    crashing.run_polling.side_effect = RuntimeError("polling died for 1:TOKEN")
    build = MagicMock(side_effect=[crashing, healthy])
    monkeypatch.setattr(main, "BuildApplication", build)
    main.Main(["--token", "1:TOKEN", "--initial-user", "42", "--users-file", str(tmp_path / "u.json")])
    assert build.call_count == 2 and healthy.run_polling.call_count == 1
    restartInfo = build.call_args_list[1][0][4]
    assert restartInfo == "restart #1 after: RuntimeError: polling died for ***"
    assert build.call_args_list[0][0][4] == ""


def test_search_client_is_created_only_when_a_key_is_given(monkeypatch, tmp_path):
    from minionbot.search.serpapi import SerpApiClient

    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE", "SERPAPI_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())
    build = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(main, "BuildApplication", build)
    base = ["--token", "1:T", "--initial-user", "42", "--users-file", str(tmp_path / "u.json")]
    main.Main(base)
    assert build.call_args[0][7] is None
    main.Main(base + ["--serpapi-key", "KEY", "--google-language", "en", "--google-country", "us"])
    client = build.call_args[0][7]
    assert isinstance(client, SerpApiClient) and (client.apiKey, client.language, client.country) == ("KEY", "en", "us")
