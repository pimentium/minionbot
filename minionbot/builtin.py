"""Built-in commands: /start, /help, /whoami, /users."""

from __future__ import annotations

from minionbot.commands import Command, CommandContext, CommandRegistry
from minionbot.users import ADMIN_PERMISSION, ParseUserRef, UserError

USERS_USAGE = (
    "Usage:\n"
    "/users list\n"
    "/users add <id|@username> [name] [perms=a,b]\n"
    "/users remove <id|@username|name>"
)


def HelpCommand(context: CommandContext) -> str:
    lines = ["Available commands:"]
    for command in context.registry.Available(context.user):
        for syntax, description in command.help:
            lines.append(f"{syntax} - {description}")
    return "\n".join(lines)


def WhoamiCommand(context: CommandContext) -> str:
    username = f"@{context.username}" if context.username else "(none)"
    access = f"granted as '{context.user.name}'" if context.user else "not granted"
    return f"Telegram ID: {context.telegramId}\nUsername: {username}\nAccess: {access}"


def UsersCommand(context: CommandContext) -> str:
    if not context.args:
        return USERS_USAGE
    action, rest = context.args[0].lower(), context.args[1:]
    if action == "list":
        return UsersList(context)
    if action == "add":
        return UsersAdd(context, rest)
    if action == "remove":
        return UsersRemove(context, rest)
    return USERS_USAGE


def UsersList(context: CommandContext) -> str:
    users = context.store.List()
    lines = [f"Users ({len(users)}):"]
    lines += [f"- {user.Describe()}" for user in users]
    return "\n".join(lines)


def UsersAdd(context: CommandContext, args: list[str]) -> str:
    if not args:
        return USERS_USAGE
    ref = ParseUserRef(args[0])
    permissions: list[str] | None = None
    names: list[str] = []
    for token in args[1:]:
        if token.lower().startswith("perms="):
            permissions = [p.strip().lower() for p in token[6:].split(",") if p.strip()]
        else:
            names.append(token)
    if len(names) > 1:
        raise UserError("Name must be a single word.")
    user = context.store.Add(ref, name=names[0] if names else None, permissions=permissions)
    note = " (will be linked to their Telegram ID on first message)" if user.telegramId is None else ""
    return f"Added {user.Describe()}{note}"


def UsersRemove(context: CommandContext, args: list[str]) -> str:
    if len(args) != 1:
        return USERS_USAGE
    user = context.store.Find(args[0])
    if user is None:
        raise UserError(f"No user matches '{args[0]}'.")
    if context.user is not None and user is context.user:
        raise UserError("You cannot remove yourself.")
    context.store.Remove(user)
    return f"Removed {user.name}."


def BuildRegistry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.Register(Command("start", HelpCommand))
    registry.Register(Command("help", HelpCommand, help=(("/help", "show commands available to you"),)))
    registry.Register(
        Command(
            "whoami",
            WhoamiCommand,
            help=(("/whoami", "show your Telegram ID and access status"),),
            public=True,
        )
    )
    registry.Register(
        Command(
            "users",
            UsersCommand,
            permission=ADMIN_PERMISSION,
            help=(
                ("/users list", "list configured users"),
                ("/users add <id|@username> [name] [perms=a,b]", "add a user (default: all permissions)"),
                ("/users remove <id|@username|name>", "remove a user"),
            ),
        )
    )
    return registry
