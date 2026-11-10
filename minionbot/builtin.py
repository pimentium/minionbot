"""Built-in commands: /start, /help, /whoami, /users, /restart, /shutdown."""

from __future__ import annotations

from minionbot.commands import Command, CommandContext, CommandRegistry
from minionbot.control import RESTART, SHUTDOWN
from minionbot.users import ASSIGNABLE_ROLES, Role, ParseRole, ParseUserRef, UserError

ROLE_NAMES = "|".join(role.label for role in ASSIGNABLE_ROLES)
USERS_USAGE = (
    "Usage:\n"
    "/users list\n"
    f"/users add <id|@username> [{ROLE_NAMES}] [name]\n"
    f"/users update <id|@username|name> <{ROLE_NAMES}>\n"
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
    if context.user is None:  # unknown role: the original, short format
        return f"Telegram ID: {context.telegramId}\nUsername: {username}\nAccess: not granted"
    role = context.user.role
    return (
        f"Telegram ID: {context.telegramId}\nUsername: {username}\nAccess: granted as '{context.user.name}'\n"
        f"Role: {role.label}\nPermissions: {', '.join(context.registry.Permissions(role))}"
    )


def UsersCommand(context: CommandContext) -> str:
    if not context.args:
        return USERS_USAGE
    action, rest = context.args[0].lower(), context.args[1:]
    if action == "list":
        return UsersList(context)
    if action == "add":
        return UsersAdd(context, rest)
    if action == "update":
        return UsersUpdate(context, rest)
    if action == "remove":
        return UsersRemove(context, rest)
    return USERS_USAGE


def UsersList(context: CommandContext) -> str:
    users = context.store.List()
    lines = [f"Users ({len(users)}):"]
    lines += [f"- {user.Describe()}" for user in users]
    return "\n".join(lines)


def UsersAdd(context: CommandContext, args: list[str]) -> str:
    """/users add <id|@username> [role] [name]  (role defaults to user)"""
    if not args or len(args) > 3:
        return USERS_USAGE
    ref = ParseUserRef(args[0])
    role = ParseRole(args[1]) if len(args) > 1 else Role.USER
    name = args[2] if len(args) > 2 else None
    user = context.store.Add(ref, name=name, role=role)
    note = " (will be linked to their Telegram ID on first message)" if user.telegramId is None else ""
    return f"Added {user.Describe()}{note}"


def UsersUpdate(context: CommandContext, args: list[str]) -> str:
    """/users update <id|@username|name> <role>  (the role is required)"""
    if len(args) != 2:
        return USERS_USAGE
    user = context.store.Find(args[0])
    if user is None:
        raise UserError(f"No user matches '{args[0]}'.")
    if context.user is not None and user is context.user:
        raise UserError("You cannot change your own role.")
    context.store.UpdateRole(user, ParseRole(args[1]))
    return f"Updated {user.Describe()}"


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


def RequestControl(context: CommandContext, action: str, text: str) -> str:
    if context.control is None:
        raise UserError("This command is not available here.")
    context.control.Request(action)
    return text


def RestartCommand(context: CommandContext) -> str:
    return RequestControl(context, RESTART, "Restarting...")


def ShutdownCommand(context: CommandContext) -> str:
    return RequestControl(context, SHUTDOWN, "Shutting down. Start the bot again on its host to bring it back.")


def BuildRegistry() -> CommandRegistry:
    """Commands and the lowest role that may use each. Roles include all lower ones."""
    registry = CommandRegistry()
    registry.Register(Command("start", HelpCommand, minRole=Role.USER))
    registry.Register(Command("help", HelpCommand, help=(("/help", "show commands available to you"),), minRole=Role.USER))
    registry.Register(Command("whoami", WhoamiCommand, help=(("/whoami", "show your Telegram ID, role and permissions"),), minRole=Role.UNKNOWN))
    registry.Register(
        Command(
            "users",
            UsersCommand,
            help=(
                ("/users list", "list configured users"),
                (f"/users add <id|@username> [{ROLE_NAMES}] [name]", "add a user (role defaults to user)"),
                (f"/users update <id|@username|name> <{ROLE_NAMES}>", "change a user's role"),
                ("/users remove <id|@username|name>", "remove a user"),
            ),
            minRole=Role.ADMIN,
        )
    )
    registry.Register(Command("restart", RestartCommand, help=(("/restart", "restart the bot"),), minRole=Role.OWNER))
    registry.Register(Command("shutdown", ShutdownCommand, help=(("/shutdown", "stop the bot"),), minRole=Role.OWNER))
    return registry
