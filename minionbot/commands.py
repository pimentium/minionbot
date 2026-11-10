"""Transport-independent command framework: registry, access control, dispatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from minionbot.users import SOCIAL_LINKS_MIN_ROLE, Role, User, UserError, UserStore

LOGGER = logging.getLogger(__name__)

UNKNOWN_COMMAND_TEXT = "Unknown command. Try /help."
INTERNAL_ERROR_TEXT = "Something went wrong."


@dataclass(frozen=True)
class CommandContext:
    store: UserStore
    registry: CommandRegistry
    telegramId: int
    username: str | None
    user: User | None
    args: list[str]
    control: object | None = None  # BotControl, to request restart/shutdown


CommandFunction = Callable[[CommandContext], str]


@dataclass(frozen=True)
class Command:
    """A bot command.

    minRole: the lowest role allowed to run it (roles include everything below them).
    help: (syntax, description) lines shown by /help; empty = hidden.
    inGroups: also available in group chats (default: private chats only).
    needsChat: implemented in the Telegram adapter because it needs async chat I/O (downloads, uploads);
        the sync handler is then only a usage fallback.
    """

    name: str
    handler: CommandFunction
    help: tuple[tuple[str, str], ...] = ()
    minRole: Role = Role.USER
    inGroups: bool = False
    needsChat: bool = False


class CommandRegistry:
    def __init__(self) -> None:
        self.commands: dict[str, Command] = {}

    def Register(self, command: Command) -> None:
        if command.name in self.commands:
            raise ValueError(f"Command '{command.name}' already registered")
        self.commands[command.name] = command

    def Names(self) -> list[str]:
        return list(self.commands)

    @staticmethod
    def RoleOf(user: User | None) -> Role:
        return user.role if user else Role.UNKNOWN

    def CanRun(self, command: Command, user: User | None) -> bool:
        return self.RoleOf(user) >= command.minRole

    def Available(self, user: User | None) -> list[Command]:
        return [c for c in self.commands.values() if self.CanRun(c, user)]

    def Permissions(self, role: Role) -> list[str]:
        """Everything a role may use: its commands, then the social link feature."""
        names = [f"/{c.name}" for c in self.commands.values() if role >= c.minRole]
        if role >= SOCIAL_LINKS_MIN_ROLE:
            names.append("social network links")
        return names

    def Authorize(self, name: str, telegramId: int, username: str | None, store: UserStore) -> tuple[User | None, str | None]:
        """Who is asking and, if they may not run /name, the text to answer with."""
        user = store.Authenticate(telegramId, username)
        command = self.commands.get(name)
        if command is None:
            return user, store.DeniedText() if user is None else UNKNOWN_COMMAND_TEXT
        if user is None and command.minRole > Role.UNKNOWN:
            return user, store.DeniedText()
        if not self.CanRun(command, user):
            return user, f"You don't have permission to use /{name} (it needs the {command.minRole.label} role)."
        return user, None

    def Dispatch(
        self,
        name: str,
        args: list[str],
        telegramId: int,
        username: str | None,
        store: UserStore,
        onError: Callable[[Exception], None] | None = None,
        control: object | None = None,
    ) -> str:
        user, denial = self.Authorize(name, telegramId, username, store)
        if denial is not None:
            return denial
        command = self.commands[name]
        context = CommandContext(store, self, telegramId, username, user, args, control)
        try:
            return command.handler(context)
        except UserError as error:
            return f"Error: {error}"
        except Exception as error:
            LOGGER.exception("Command /%s failed", name)
            if onError is not None:
                onError(error)
            return INTERNAL_ERROR_TEXT

    def DispatchFallback(self, telegramId: int, username: str | None, store: UserStore) -> str:
        """Reply for anything that is not a registered command."""
        user = store.Authenticate(telegramId, username)
        return store.DeniedText() if user is None else UNKNOWN_COMMAND_TEXT
