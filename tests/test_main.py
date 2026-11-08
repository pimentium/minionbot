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
    main.Main(["--token", "123:FAKE", "--initial-user", "42", "--users-file", str(usersFile)])
    config = build.call_args[0][0]
    assert config.botToken == "123:FAKE" and config.initialUser == "42"
    assert '"telegramId": 42' in usersFile.read_text()
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
