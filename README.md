# minionbot

A personal Telegram bot. It ignores everyone except users you add explicitly.

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# pass options on the command line (see below), or put BOT_TOKEN=... and INITIAL_USER=... into a .env file
.venv/bin/python main.py --token <BOT_TOKEN> --initial-user <YOUR_TELEGRAM_ID>
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

## Social network downloads

Send (or forward) a link to a post and the bot replies with the post's images/videos, its text and
the author. One reply per link. Supported: Instagram, Facebook, Twitter/X, Threads. For Twitter/X the
author's thread is combined into one reply (all parts' text in order, all media). Threads: the author's
parts shown on the post page are combined.

The bot reacts to:
- any message in a private chat that contains such links;
- in a group: a message that **@mentions the bot** and contains links;
- in a group: a **reply** to a message with links whose text mentions the bot (typically just `@botname`).

Only added users are served; everyone else gets "Please ask my owner @owner to allow me to help you too."
The owner is the initial user (their @username is learned when they first message the bot). Group
commands (`/users`, ...) are ignored; commands work in private chats only.

**Groups need privacy mode off.** By default Telegram delivers to a bot in a group only commands and
replies to the bot's own messages, so an `@botname` message or an `@botname` reply to someone else's message
may never arrive. Fix: in @BotFather send `/setprivacy`, pick the bot, choose **Disable**, then **remove the
bot from the group and add it again** (or make it a group admin). At startup the bot logs whether privacy
mode is ON or OFF, and every received message is logged (chat type, user id, decision, number of links).

### Accounts

Networks block anonymous access, so give the bot a logged-in **browser session**. Username/password login
is not supported (it is flaky, triggers 2FA/challenges, and gallery-dl dropped it for Twitter). All options
are optional; command-line values win over env vars / `.env`.

| Network | Option (env var) | Where to get it |
|---|---|---|
| Instagram | `--instagram-session-id` (`INSTAGRAM_SESSION_ID`) | value of the `sessionid` cookie on instagram.com |
| Twitter/X | `--twitter-auth-token` (`TWITTER_AUTH_TOKEN`) | value of the `auth_token` cookie on x.com |
| Facebook | `--facebook-cookies FILE` (`FACEBOOK_COOKIES_FILE`) | Netscape cookies file exported from your browser |
| Threads | none | public posts are read anonymously (best effort) |

Cookie values: browser dev tools, Application (Chrome) / Storage (Firefox), Cookies. `--twitter-cookies` and
`--instagram-cookies` (files) also exist. These values work like passwords: keep them out of git and chat,
and prefer env vars/`.env` over the command line (visible in `ps`). Logging out in that browser invalidates
them, and a bot sharing a session with your browser can get you logged out there, so use a separate profile
or account. Without a Twitter session only single public tweets work (no threads).

Videos are sent with their real width/height/duration (read from the MP4 header) so Telegram shows the
correct aspect ratio. Videos over 50 MB (Telegram's bot limit) and photos over 10 MB are skipped with a note
and the original link. Albums are sent in groups of 10. Text that doesn't fit a 1024-char caption follows as
messages.

## Owner notifications and reliability

- **Start-up message:** every time the bot starts it messages the owner: host name, OS, whether it runs in
  Docker, Python version, pid, bot name, number of configured users and the group privacy mode. The owner must
  have written to the bot at least once (Telegram doesn't let bots start conversations); if the owner was added
  by `@username` only, the message is skipped until their numeric ID is known (the log says so).
- **Failure messages:** unhandled errors (a bug while handling a message or command, an unexpected download or
  send failure, a polling error such as two instances running at once) are sent to the owner with where it
  happened, the error and the tail of the traceback. Expected problems (private post, expired session, network
  blips) are not sent. The same error is reported at most once per 10 minutes. Secrets are redacted.
- **Auto-restart:** if the bot crashes, it is rebuilt and started again after 2, 4, 8 ... up to 300 seconds
  (the counter resets after a run that lasted a minute); the next start-up message says
  `Restarted: restart #N after: <error>`. An invalid bot token is fatal (exit code 1, no retry). Ctrl+C / SIGTERM
  stop the bot normally.
- **Hard crashes:** if the whole process dies (out of memory, killed), only an outer supervisor can restart it.
  In Docker use `restart: unless-stopped`; with systemd use `Restart=always`.
- **Logs:** the bot token and session cookies are redacted from all log output (python-telegram-bot itself
  prints the token in some error messages).

## Tests

```bash
.venv/bin/python -m pytest
```

Tests use mocks only; nothing talks to Telegram.
