# minionbot

A personal Telegram bot. It ignores everyone except users you add explicitly.

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # then edit: BOT_TOKEN, INITIAL_USER
.venv/bin/python main.py
```

- `BOT_TOKEN`: create a bot with [@BotFather](https://t.me/BotFather).
- `INITIAL_USER`: your numeric Telegram ID (or `@username`). Don't know it? Start the bot with any
  value, send `/whoami` to it from your account (works even before you have access), then fix `.env`
  and restart. The initial user is created on start if missing and has all permissions.
- `USERS_FILE` (optional): defaults to `data/users.json`. Keep it on a volume when running in Docker.

Command-line arguments override the environment / `.env`:

```bash
.venv/bin/python main.py --token <BOT_TOKEN> --initial-user 123456789 [--users-file data/users.json]
```

Note: a token passed on the command line is visible in `ps` output and shell history; prefer `.env`
or environment variables on shared machines.

All config can also be set via environment variables, so Docker works with `--env-file .env` and a mounted `data/`.

## Commands

| Command | Who | What |
|---|---|---|
| `/help` | users | list the commands available to you |
| `/whoami` | anyone | show your Telegram ID, username and access status |
| `/users list` | `admin` | list users |
| `/users add <id\|@username> [name] [perms=a,b]` | `admin` | add a user (default permissions: all) |
| `/users remove <id\|@username\|name>` | `admin` | remove a user (not yourself, not the last one) |

Users added by `@username` are linked to their numeric ID on first message; after that only the ID
counts, so a re-assigned username gives no access. Prefer numeric IDs.

Permissions: `*` means everything (the default, so for now every user can run every command);
`/users` requires `admin`. To restrict someone later: `/users add 123 bob perms=notes`.

Strangers get "Access denied." for everything except `/whoami`. The bot only answers in private chats.

## Tests

```bash
.venv/bin/python -m pytest
```

Tests use mocks only; nothing talks to Telegram.
