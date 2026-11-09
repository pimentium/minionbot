from unittest.mock import AsyncMock, MagicMock

import pytest

from minionbot.notify import Notifier
from minionbot.users import UserRef, UserStore


@pytest.fixture
def ownerStore(tmp_path):
    store = UserStore(tmp_path / "u.json")
    store.EnsureInitialUser("1001")
    store.Add(UserRef(telegramId=2), name="friend")
    return store


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def MakeNotifier(store, clock=None, secrets=("S3CRET",)):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return Notifier(bot, store, secrets, clock=clock or Clock(), describeRuntime=lambda: "Host: testbox"), bot


async def test_startup_message_has_host_and_user_count(ownerStore):
    notifier, bot = MakeNotifier(ownerStore)
    assert await notifier.NotifyStartup("minionbot", privacyOn=True)
    chatId, text = bot.send_message.call_args[0]
    assert chatId == 1001
    assert "Host: testbox" in text and "Users configured: 2" in text and "@minionbot" in text and "privacy mode: ON" in text
    assert "Restarted" not in text


async def test_startup_message_mentions_restart_reason(ownerStore):
    notifier, bot = MakeNotifier(ownerStore)
    await notifier.NotifyStartup("minionbot", restartInfo="restart #2 after: NetworkError: down")
    assert "Restarted: restart #2 after: NetworkError: down" in bot.send_message.call_args[0][1]


async def test_no_notification_when_owner_id_unknown(tmp_path):
    store = UserStore(tmp_path / "u.json")
    store.EnsureInitialUser("@ownername")
    notifier, bot = MakeNotifier(store)
    assert await notifier.NotifyStartup("minionbot") is False
    bot.send_message.assert_not_awaited()


async def test_send_failure_never_raises_and_hides_secrets(ownerStore, caplog):
    notifier, bot = MakeNotifier(ownerStore)
    bot.send_message.side_effect = RuntimeError("Forbidden S3CRET")
    assert await notifier.Send("hi") is False
    assert "S3CRET" not in caplog.text


async def test_failure_report_contains_details_and_no_secrets(ownerStore):
    notifier, bot = MakeNotifier(ownerStore)
    try:
        raise ValueError("boom S3CRET")
    except ValueError as error:
        assert await notifier.ReportFailure("handling a message", error)
    text = bot.send_message.call_args[0][1]
    assert "handling a message" in text and "ValueError: boom ***" in text and "Traceback" in text
    assert "S3CRET" not in text and "Host: testbox" in text


async def test_same_error_is_rate_limited_then_reported_again(ownerStore):
    clock = Clock()
    notifier, bot = MakeNotifier(ownerStore, clock)
    error = RuntimeError("same")
    assert await notifier.ReportFailure("x", error)
    clock.now = 10
    assert not await notifier.ReportFailure("x", error)
    assert not await notifier.ReportFailure("x", error)
    assert await notifier.ReportFailure("x", RuntimeError("different"))
    clock.now = 700
    assert await notifier.ReportFailure("x", error)
    assert "suppressed 2 time(s)" in bot.send_message.call_args[0][1]


async def test_message_is_truncated(ownerStore):
    notifier, bot = MakeNotifier(ownerStore)
    await notifier.Send("x" * 10000)
    assert len(bot.send_message.call_args[0][1]) <= 3900


def test_local_ip_is_an_address_or_unknown():
    import ipaddress

    from minionbot.notify import LocalIp

    value = LocalIp()
    assert value == "unknown" or ipaddress.ip_address(value)


def test_local_ip_unknown_when_no_route(monkeypatch):
    import socket

    from minionbot import notify

    class Broken:
        def __enter__(self):
            raise OSError("Network is unreachable")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(notify.socket, "socket", lambda *args: Broken())
    assert notify.LocalIp() == "unknown"


def test_runtime_description_includes_ip_and_host():
    from minionbot.notify import DescribeRuntime

    text = DescribeRuntime()
    assert "IP address: " in text and "Host: " in text
