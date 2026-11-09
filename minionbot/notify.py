"""Messages from the bot to its owner: startup info and unhandled failures."""

from __future__ import annotations

import logging
import os
import platform
import socket
import time
import traceback
from typing import Callable, Iterable

from minionbot.users import UserStore

LOGGER = logging.getLogger(__name__)

MESSAGE_LIMIT = 3900  # Telegram allows 4096
TRACEBACK_TAIL = 1200
COOLDOWN_SECONDS = 600


def LocalIp() -> str:
    """The IP address this machine uses for outgoing traffic. Sends no packets (UDP connect only picks a route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address: never contacted
            return probe.getsockname()[0]
    except OSError:
        return "unknown"


def DescribeRuntime() -> str:
    inDocker = os.path.exists("/.dockerenv")
    return (
        f"Host: {socket.gethostname()} ({platform.system()} {platform.release()}, {platform.machine()})"
        f"{', in Docker' if inDocker else ''}\n"
        f"IP address: {LocalIp()}\n"
        f"Python {platform.python_version()}, pid {os.getpid()}"
    )


class Notifier:
    def __init__(
        self,
        bot,
        store: UserStore,
        secrets: Iterable[str] = (),
        cooldownSeconds: float = COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        describeRuntime: Callable[[], str] = DescribeRuntime,
    ):
        self.bot = bot
        self.store = store
        self.secrets = [secret for secret in secrets if secret]
        self.cooldownSeconds = cooldownSeconds
        self.clock = clock
        self.describeRuntime = describeRuntime
        self.lastSent: dict[tuple, float] = {}
        self.suppressed: dict[tuple, int] = {}

    def Scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    async def Send(self, text: str) -> bool:
        """Send text to the owner. Never raises: a broken notification must not break the bot."""
        owner = self.store.GetOwner()
        if owner is None or owner.telegramId is None:
            LOGGER.warning("Cannot notify the owner: their Telegram ID is not known yet (they must message the bot once)")
            return False
        try:
            await self.bot.send_message(owner.telegramId, self.Scrub(text)[:MESSAGE_LIMIT])
            return True
        except Exception as error:
            LOGGER.warning("Could not notify the owner (%s): %s", type(error).__name__, self.Scrub(str(error))[:200])
            return False

    async def NotifyStartup(self, botUsername: str, privacyOn: bool | None = None, restartInfo: str = "") -> bool:
        lines = [
            f"✅ minionbot started (@{botUsername})",
            self.describeRuntime(),
            f"Users configured: {len(self.store.List())}",
        ]
        if privacyOn is not None:
            lines.append(f"Group privacy mode: {'ON' if privacyOn else 'OFF'}")
        if restartInfo:
            lines.append(f"Restarted: {restartInfo}")
        return await self.Send("\n".join(lines))

    async def ReportFailure(self, where: str, error: BaseException) -> bool:
        """Tell the owner about an unhandled error. The same error is reported at most once per cooldown."""
        key = (where, type(error).__name__, str(error)[:80])
        now = self.clock()
        last = self.lastSent.get(key)
        if last is not None and now - last < self.cooldownSeconds:
            self.suppressed[key] = self.suppressed.get(key, 0) + 1
            return False
        self.lastSent[key] = now
        repeated = self.suppressed.pop(key, 0)
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        lines = [
            "⚠️ minionbot failure",
            f"Where: {where}",
            f"Error: {type(error).__name__}: {str(error)[:400]}",
            self.describeRuntime(),
        ]
        if repeated:
            lines.append(f"(The same error was suppressed {repeated} time(s) meanwhile.)")
        lines += ["", "Traceback (tail):", trace[-TRACEBACK_TAIL:]]
        return await self.Send("\n".join(lines))
