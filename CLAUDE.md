# CLAUDE.md

Personal Telegram bot (python-telegram-bot v22, polling). See README.md for usage.

## Hard rules
- Python. Classes/methods/functions in CamelCase, locals/params/attributes in camelCase, globals in CASE.
- Never commit or push without explicit owner confirmation. Never put secrets in any committable file
  (use placeholders); don't read `.env` or `data/` (git-ignored state).
- Every new script/change needs pytest tests (external services mocked). Always state what was verified
  against real Telegram vs only mocked.
- Ask clarifying questions / push back before building; recommend, don't survey.
- Keep it runnable locally and Docker-friendly (env-based config, state under `data/`).

## Layout
- `minionbot/users.py`: User model + JSON `UserStore` (atomic writes, 0600).
- `minionbot/commands.py`: transport-independent `CommandRegistry.Dispatch` (auth + permissions + errors).
- `minionbot/builtin.py`: /start /help /whoami /users; `BuildRegistry()` is where new commands go.
- `minionbot/bot.py`: thin telegram adapter. `main.py` (repo root) and `python -m minionbot.main`: entry points.

## Decisions
- Users identified by numeric ID or @username; username entries bind to the ID on first contact.
- Admin = `admin` permission flag (no secret typed in chat). Default permission `*` = all commands,
  so currently every user is effectively admin (requested). Restrict via `perms=`.
- Storage: JSON file (chosen over SQLite by owner). Corrupt file => refuse to start, never overwrite.
- Unknown users: "Access denied." for everything but `/whoami`. Private chats only.
- Config precedence: CLI args (`--token`, `--initial-user`, `--users-file`) > env/.env > defaults.
- httpx logging is lowered to WARNING because its URLs contain the bot token.
- New commands: write `Handler(context) -> str`, register in `BuildRegistry` with help lines/permission.

## Not yet
Dockerfile/compose, real-Telegram verification.
