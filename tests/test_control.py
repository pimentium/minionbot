import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from minionbot import main
from minionbot.bot import MakeCommandCallback
from minionbot.control import RESTART, SHUTDOWN, BotControl
from minionbot.users import UserRef

from tests.conftest import OWNER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeUpdate


def Run(registry, store, text, telegramId=OWNER_ID, control=None):
    name, *args = text.lstrip("/").split()
    return registry.Dispatch(name, args, telegramId, None, store, control=control)


def test_owner_can_restart_and_shutdown(registry, store):
    control = BotControl()
    assert Run(registry, store, "/restart", control=control) == "Restarting..."
    assert control.requested == RESTART
    assert "Shutting down" in Run(registry, store, "/shutdown", control=control)
    assert control.requested == SHUTDOWN


def test_other_users_even_with_all_permissions_cannot(registry, store):
    store.Add(UserRef(telegramId=2), name="friend")  # default permissions: "*"
    control = BotControl()
    for command in ("/restart", "/shutdown"):
        assert "permission" in Run(registry, store, command, telegramId=2, control=control)
    assert control.requested is None


def test_strangers_are_denied(registry, store):
    control = BotControl()
    assert Run(registry, store, "/shutdown", telegramId=777, control=control) == store.DeniedText()
    assert control.requested is None


def test_help_lists_owner_commands_only_for_owner(registry, store):
    store.Add(UserRef(telegramId=2), name="friend")
    assert "/restart" in Run(registry, store, "/help") and "/shutdown" in Run(registry, store, "/help")
    friendHelp = Run(registry, store, "/help", telegramId=2)
    assert "/restart" not in friendHelp and "/shutdown" not in friendHelp and "/help" in friendHelp


def test_without_control_the_command_reports_unavailable(registry, store):
    assert Run(registry, store, "/restart").startswith("Error")


async def test_callback_stops_application_after_replying(store, registry):
    control = BotControl()
    context = FakeContext([])
    update = FakeUpdate(FakeMessage("/shutdown"))
    await MakeCommandCallback(registry, store, "shutdown", None, control)(update, context)
    update.effective_message.reply_text.assert_awaited_once()
    context.application.stop_running.assert_called_once()


async def test_callback_does_not_stop_for_normal_commands_or_denied(store, registry):
    control = BotControl()
    context = FakeContext([])
    await MakeCommandCallback(registry, store, "help", None, control)(FakeUpdate(FakeMessage("/help")), context)
    store.Add(UserRef(telegramId=2), name="friend")
    await MakeCommandCallback(registry, store, "restart", None, control)(FakeUpdate(FakeMessage("/restart"), 2), context)
    context.application.stop_running.assert_not_called()


# ---- main: what happens after the application stopped ----------------------------------------

def PatchMain(monkeypatch, tmp_path, requested):
    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE", main.RESTART_REASON_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())

    def Build(config, store, registry, service, restartInfo, control):
        application = MagicMock()
        application.run_polling.side_effect = lambda: setattr(control, "requested", requested)
        Build.restartInfo = restartInfo
        return application

    monkeypatch.setattr(main, "BuildApplication", Build)
    execve = MagicMock()
    monkeypatch.setattr(main.os, "execve", execve)
    return execve, Build


ARGS = lambda tmp_path: ["--token", "1:T", "--initial-user", "42", "--users-file", str(tmp_path / "u.json")]


def test_shutdown_exits_without_reexec(monkeypatch, tmp_path):
    execve, _ = PatchMain(monkeypatch, tmp_path, SHUTDOWN)
    main.Main(ARGS(tmp_path))
    execve.assert_not_called()


def test_restart_reexecs_same_command_with_original_env(monkeypatch, tmp_path):
    execve, _ = PatchMain(monkeypatch, tmp_path, RESTART)
    monkeypatch.setenv("KEEP_ME", "1")
    main.Main(ARGS(tmp_path))
    executable, argv, env = execve.call_args[0]
    assert executable == sys.executable and argv[0] == sys.executable and argv[1:] == sys.orig_argv[1:]
    assert env["KEEP_ME"] == "1" and env[main.RESTART_REASON_ENV] == main.RESTART_REASON


def test_restarted_process_reports_the_reason(monkeypatch, tmp_path):
    _, build = PatchMain(monkeypatch, tmp_path, SHUTDOWN)
    monkeypatch.setenv(main.RESTART_REASON_ENV, main.RESTART_REASON)
    main.Main(ARGS(tmp_path))
    assert build.restartInfo == main.RESTART_REASON
    assert main.RESTART_REASON_ENV not in os.environ  # consumed, so a later plain start doesn't repeat it


def test_failed_exec_exits_with_error(monkeypatch, tmp_path):
    execve, _ = PatchMain(monkeypatch, tmp_path, RESTART)
    execve.side_effect = OSError("exec failed")
    with pytest.raises(SystemExit) as exit_:
        main.Main(ARGS(tmp_path))
    assert exit_.value.code == 1
