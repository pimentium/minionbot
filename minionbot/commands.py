"""Transport-independent command framework: registry, access control, dispatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from minionbot.users import User, UserError, UserStore

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


CommandFunction = Callable[[CommandContext], str]


@dataclass(frozen=True)
class Command:
    """A bot command.

    permission: required user permission (None = any configured user).
    public: callable even by unknown users (only for harmless commands).
    help: (syntax, description) lines shown by /help; empty = hidden.
    """

    name: str
    handler: CommandFunction
    help: tuple[tuple[str, str], ...] = ()
    permission: str | None = None
    public: bool = False


class CommandRegistry:
    def __init__(self) -> None:
        self.commands: dict[str, Command] = {}

    def Register(self, command: Command) -> None:
        if command.name in self.commands:
            raise ValueError(f"Command '{command.name}' already registered")
        self.commands[command.name] = command

    def Names(self) -> list[str]:
        return list(self.commands)

    def CanRun(self, command: Command, user: User | None) -> bool:
        if command.public:
            return True
        if user is None:
            return False
        return command.permission is None or user.HasPermission(command.permission)

    def Available(self, user: User | None) -> list[Command]:
        return [c for c in self.commands.values() if self.CanRun(c, user)]

    def Dispatch(
        self,
        name: str,
        args: list[str],
        telegramId: int,
        username: str | None,
        store: UserStore,
        onError: Callable[[Exception], None] | None = None,
    ) -> str:
        user = store.Authenticate(telegramId, username)
        command = self.commands.get(name)
        if command is None:
            return store.DeniedText() if user is None else UNKNOWN_COMMAND_TEXT
        if not command.public and user is None:
            return store.DeniedText()
        if not self.CanRun(command, user):
            return f"You don't have permission to use /{name}."
        context = CommandContext(store, self, telegramId, username, user, args)
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
