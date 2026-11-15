"""Access requests (/friend): who is waiting for the owner's answer, and who was turned down recently."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from minionbot.users import NAME_PATTERN

DENIED_COOLDOWN = 24 * 3600  # seconds a denied person must wait before asking again (stops nagging the owner)

NEW, PENDING, COOLDOWN = "new", "pending", "cooldown"


@dataclass(frozen=True)
class FriendRequest:
    telegramId: int
    username: str | None
    fullName: str
    chatId: int | None = None  # where the request was made (a group, or the private chat); the answer goes there
    messageId: int | None = None

    def Describe(self) -> str:
        username = f"@{self.username}" if self.username else "(no username)"
        return f"{self.fullName or '(no name)'}, {username}, Telegram ID {self.telegramId}"

    def NameCandidates(self) -> list[str]:
        """Names for the new user, nicest first; the last one (the ID) always works unless taken."""
        cleaned = re.sub(r"[^\w.-]+", "_", self.fullName).strip("_")[:32]
        candidates = [self.username or "", cleaned, str(self.telegramId), f"user{self.telegramId}"]
        return [name for name in candidates if name and NAME_PATTERN.match(name)]


class FriendRequests:
    """In-memory (a restart forgets pending requests; the person can simply ask again)."""

    def __init__(self, cooldown: float = DENIED_COOLDOWN, clock: Callable[[], float] = time.monotonic):
        self.cooldown, self.clock = cooldown, clock
        self.pending: dict[int, FriendRequest] = {}
        self.deniedAt: dict[int, float] = {}

    def Submit(self, request: FriendRequest) -> str:
        if request.telegramId in self.pending:
            return PENDING
        denied = self.deniedAt.get(request.telegramId)
        if denied is not None and self.clock() - denied < self.cooldown:
            return COOLDOWN
        self.pending[request.telegramId] = request
        return NEW

    def Take(self, telegramId: int) -> FriendRequest | None:
        return self.pending.pop(telegramId, None)

    def Deny(self, telegramId: int) -> None:
        self.pending.pop(telegramId, None)
        self.deniedAt[telegramId] = self.clock()
