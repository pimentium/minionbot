"""Built-in commands: /start, /help, /whoami, /users, /version, /restart, /shutdown."""

from __future__ import annotations

import html

from minionbot.commands import Command, CommandContext, CommandRegistry, HelpEntry
from minionbot.control import RESTART, SHUTDOWN
from minionbot.version import DescribeVersion
from minionbot.users import ASSIGNABLE_ROLES, Role, ParseRole, ParseUserRef, UserError

ROLE_NAMES = "|".join(role.label for role in ASSIGNABLE_ROLES)
USERS_USAGE = (
    "Usage:\n"
    "/users list\n"
    f"/users add <id|@username> [{ROLE_NAMES}] [name]\n"
    f"/users update <id|@username|name> <{ROLE_NAMES}>\n"
    "/users remove <id|@username|name>"
)


ROLE_TITLES = {
    Role.OWNER: "Owner commands",
    Role.ADMIN: "Admin commands",
    Role.USER: "User commands",
    Role.UNKNOWN: "Everyone",
}


def FormatHelpEntry(entry: HelpEntry) -> str:
    """Command line (monospace), then its description indented as a quote: summary first, details after it."""
    lines = [html.escape(entry.summary, quote=False)] + [f"• {html.escape(detail, quote=False)}" for detail in entry.details]
    return f"<code>{html.escape(entry.syntax, quote=False)}</code>\n<blockquote>" + "\n".join(lines) + "</blockquote>"


def HelpCommand(context: CommandContext) -> str:
    """Telegram HTML. Sections by role, the least restricted first; entries are separated by blank lines (the
    adapter splits long help there, a section title always stays attached to its first entry)."""
    available = context.registry.Available(context.user)
    blocks: list[str] = []
    for role in sorted({command.minRole for command in available}):
        entries = [entry for command in available if command.minRole == role for entry in command.help]
        for index, entry in enumerate(entries):
            title = f"<b>{ROLE_TITLES[role]}</b>\n" if index == 0 else ""
            blocks.append(title + FormatHelpEntry(entry))
    return "Available commands. Tap a command to copy it; <i>&lt;...&gt;</i> is what you fill in.\n\n" + "\n\n".join(blocks)


def WhoamiCommand(context: CommandContext) -> str:
    username = f"@{context.username}" if context.username else "(none)"
    if context.user is None:  # unknown role: the original, short format
        return f"Telegram ID: {context.telegramId}\nUsername: {username}\nAccess: not granted"
    role = context.user.role
    return (
        f"Telegram ID: {context.telegramId}\nUsername: {username}\nAccess: granted as '{context.user.name}'\n"
        f"Role: {role.label}\nPermissions: {', '.join(context.registry.Permissions(role))}"
    )


def VersionCommand(context: CommandContext) -> str:
    return f"Version: {DescribeVersion()}"


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


SOCIAL_USAGE = "Usage: /social <link to a post on Instagram, Facebook, Twitter/X or Threads>"


def SocialCommand(context: CommandContext) -> str:
    """Placeholder: /social needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return SOCIAL_USAGE


URL_USAGE = "Usage: /url <link to a web page>"
GOOGLE_USAGE = "Usage: /google <what to search for>"
GEMINI_USAGE = "Usage: /gemini <your question or prompt>"
RUS_USAGE = "Usage: /rus <link to a web page or text>, or reply to a message with /rus  (translates to Russian)"
ENG_USAGE = "Usage: /eng <link to a web page or text>, or reply to a message with /eng  (translates to English)"


def UrlCommand(context: CommandContext) -> str:
    """Placeholder: /url needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return URL_USAGE


def GoogleCommand(context: CommandContext) -> str:
    """Placeholder: /google needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return GOOGLE_USAGE


def GeminiCommand(context: CommandContext) -> str:
    """Placeholder: /gemini needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return GEMINI_USAGE


FRIEND_USAGE = "Send /friend to ask my owner for access."


def FriendCommand(context: CommandContext) -> str:
    """Placeholder: /friend needs chat I/O (it messages the owner), so the Telegram adapter runs it (see bot.py)."""
    return FRIEND_USAGE


def RusCommand(context: CommandContext) -> str:
    """Placeholder: /rus needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return RUS_USAGE


def EngCommand(context: CommandContext) -> str:
    """Placeholder: /eng needs chat I/O, so the Telegram adapter runs it (see bot.py)."""
    return ENG_USAGE


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
    registry.Register(Command("start", HelpCommand, minRole=Role.USER, html=True))
    registry.Register(
        Command(
            "help",
            HelpCommand,
            help=(
                HelpEntry(
                    "/help",
                    "Show the commands you may use.",
                    ("Commands that need a higher role than yours are hidden.", "/start shows the same list."),
                ),
            ),
            minRole=Role.USER,
            html=True,
        )
    )
    registry.Register(
        Command(
            "whoami",
            WhoamiCommand,
            help=(
                HelpEntry(
                    "/whoami",
                    "Show your Telegram ID, role and permissions.",
                    ("Works even if I don't know you yet: send the ID to my owner to get access.",),
                ),
            ),
            minRole=Role.UNKNOWN,
        )
    )
    registry.Register(
        Command(
            "friend",
            FriendCommand,
            help=(
                HelpEntry(
                    "/friend",
                    "Ask my owner to give you access.",
                    (
                        "For people I don't know yet: I forward your request to my owner, who can approve or deny it. I tell you the answer.",
                        "A denied request can be repeated after 24 hours.",
                        "Works in groups too (the answer is posted there as a reply), also as '@name /friend'.",
                    ),
                ),
            ),
            minRole=Role.UNKNOWN,
            inGroups=True,
            needsChat=True,
        )
    )
    registry.Register(
        Command(
            "social",
            SocialCommand,
            help=(
                HelpEntry(
                    "/social <link>",
                    "Download a post from Instagram, Facebook, Twitter/X or Threads.",
                    (
                        "Replies with the post's photos, videos, text and author. Twitter and Threads thread parts come back as one reply.",
                        "Without a link in the command, the message you reply to is used.",
                        "Links sent in a private chat, or together with my @name, are handled without any command.",
                        "Files over Telegram's size limit are skipped.",
                        "Works in groups, also as '@name /social <link>'.",
                    ),
                ),
            ),
            minRole=Role.USER,
            inGroups=True,
            needsChat=True,
        )
    )
    registry.Register(
        Command(
            "url",
            UrlCommand,
            help=(
                HelpEntry(
                    "/url <link>",
                    "Read a web page in reader mode as an Instant View article.",
                    (
                        "Strips ads and clutter, publishes the text on Telegraph and replies with the link.",
                        "Paywalled or blocked pages fall back to an Internet Archive copy when one exists.",
                        "Without a link in the command, the message you reply to is used.",
                        "Any web link sent in a private chat, or together with my @name, is treated like /url.",
                        "Social network links are not read here: use /social.",
                    ),
                ),
            ),
            minRole=Role.USER,
            inGroups=True,
            needsChat=True,
        )
    )
    for name, handler, language in (("rus", RusCommand, "Russian"), ("eng", EngCommand, "English")):
        registry.Register(
            Command(
                name,
                handler,
                help=(
                    HelpEntry(
                        f"/{name} <link or text>",
                        f"Translate a web page or a text to {language} (Gemini).",
                        (
                            f"With a link: like /url, but the article is translated to {language} before it is published.",
                            "With text after the command: translates that text and replies with it.",
                            "As a reply, without a link or text of your own: translates the message you replied to (only the part you selected, if you used Telegram's quote).",
                            f"If a link or text is already in {language}, nothing is translated.",
                            "Works in groups, also as '@name /" + name + " ...'.",
                        ),
                    ),
                ),
                minRole=Role.USER,
                inGroups=True,
                needsChat=True,
            )
        )
    registry.Register(
        Command(
            "gemini",
            GeminiCommand,
            help=(
                HelpEntry(
                    "/gemini <prompt>",
                    "Ask Gemini anything.",
                    (
                        "Everything after the command is the prompt, line breaks included (up to 8,000 characters).",
                        "As a reply: the text of the message you replied to (or your selected quote) is added at the end of the prompt.",
                        "As a reply to a picture, sticker, video, GIF or video note (up to 15 MB): the file is sent too. A bare reply to a picture asks what is in it.",
                        "No memory: every /gemini is a fresh question.",
                    ),
                ),
            ),
            minRole=Role.USER,
            inGroups=True,
            needsChat=True,
        )
    )
    registry.Register(
        Command(
            "google",
            GoogleCommand,
            help=(
                HelpEntry(
                    "/google <query>",
                    "Search Google.",
                    (
                        "Replies with Google's AI overview (only when Google shows one for the query), then the top results with a snippet each.",
                        "Only the text after the command is searched; a replied-to message is not used.",
                    ),
                ),
            ),
            minRole=Role.USER,
            inGroups=True,
            needsChat=True,
        )
    )
    registry.Register(
        Command(
            "users",
            UsersCommand,
            help=(
                HelpEntry("/users list", "List the configured users with their roles."),
                HelpEntry(
                    f"/users add <id|@username> [{ROLE_NAMES}] [name]",
                    "Add a user.",
                    (
                        "The role defaults to user.",
                        "A user added by @username is linked to their Telegram ID on their first message.",
                    ),
                ),
                HelpEntry(
                    f"/users update <id|@username|name> <{ROLE_NAMES}>",
                    "Change a user's role.",
                    ("You cannot change your own role or the owner's.",),
                ),
                HelpEntry(
                    "/users remove <id|@username|name>",
                    "Remove a user.",
                    ("You cannot remove yourself or the owner.",),
                ),
            ),
            minRole=Role.ADMIN,
        )
    )
    registry.Register(
        Command(
            "version",
            VersionCommand,
            help=(HelpEntry("/version", "Show the running version.", ("The commit and its date; 'development' when not deployed.",)),),
            minRole=Role.ADMIN,
        )
    )
    registry.Register(
        Command(
            "restart",
            RestartCommand,
            help=(HelpEntry("/restart", "Restart the bot.", ("Reloads the code and the .env file; the process keeps its PID.",)),),
            minRole=Role.OWNER,
        )
    )
    registry.Register(
        Command(
            "shutdown",
            ShutdownCommand,
            help=(HelpEntry("/shutdown", "Stop the bot.", ("It stays off until it is started again on its host.",)),),
            minRole=Role.OWNER,
        )
    )
    return registry
