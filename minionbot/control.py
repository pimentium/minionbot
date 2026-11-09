"""Lets a command ask the running bot to stop or restart itself."""

from __future__ import annotations

RESTART = "restart"
SHUTDOWN = "shutdown"


class BotControl:
    def __init__(self) -> None:
        self.requested: str | None = None

    def Request(self, action: str) -> None:
        self.requested = action
