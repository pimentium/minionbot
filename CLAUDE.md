# CLAUDE.md

Personal Telegram bot (python-telegram-bot v22, polling). See README.md for usage.

## Hard rules
- Python. Classes/methods/functions in CamelCase, locals/params/attributes in camelCase, globals in CASE.
- Never commit or push without explicit owner confirmation. Never put secrets in any committable file
  (use placeholders); don't read `.env` or `data/` (git-ignored state).
- Every new script/change needs pytest tests (external services mocked). Always state what was verified
  against real Telegram vs only mocked.
- Ask clarifying questions / push back before building; recommend, don't survey.
- No `.env.example`: do not create, use, update or document one (owner's decision). `.env` itself is still
  read if present.
- Keep it runnable locally and Docker-friendly (env-based config, state under `data/`).

## Layout
- `minionbot/users.py`: User model + JSON `UserStore` (atomic writes, 0600).
- `minionbot/commands.py`: transport-independent `CommandRegistry.Dispatch` (auth + role check + errors).
- `minionbot/builtin.py`: /start /help /whoami /users; `BuildRegistry()` is where new commands go.
- `minionbot/bot.py`: thin telegram adapter. `main.py` (repo root) and `python -m minionbot.main`: entry points.

- `minionbot/notify.py`: `Notifier` (owner messages: startup, failures; scrubs secrets; 10 min cooldown per error).
  `minionbot/supervisor.py`: `RunForever` restart loop with backoff. `bot.py` has the PTB error handler and
  `RedactingFilter` (log redaction).
- `minionbot/social/`: `links.py` (URL classification), `triggers.py` (when to react / which links),
  `instagram.py` (instagrapi, sessionid cookie), `gallerydl.py` (Twitter/Facebook + Twitter thread merge), `threads.py`
  (custom page reader), `fetch.py` (media download + size limits), `videoinfo.py` (pure-Python MP4 width/height/duration), `format.py`, `service.py`, `factory.py`.

## Decisions
- Users identified by numeric ID or @username; username entries bind to the ID on first contact.
- Roles replace permissions: `Role` IntEnum UNKNOWN < USER < ADMIN < OWNER (in `users.py`), each includes the
  lower ones. A `Command` has `minRole`; social link handling needs `SOCIAL_LINKS_MIN_ROLE` (USER). OWNER is
  assigned only by `UserStore.EnsureInitialUser` (the --initial-user at start; the previous owner becomes
  admin) and can't be assigned/changed/removed by commands. Nobody can change their own role. `/whoami` for
  UNKNOWN keeps the short original format. Old files migrate without gaining privileges (owner flag -> owner,
  explicit "admin" permission -> admin, else user). `perms=` and permission strings no longer exist.
- Storage: JSON file (chosen over SQLite by owner). Corrupt file => refuse to start, never overwrite.
- Unknown users: "Access denied." for everything but `/whoami`. Private chats only.
- Config precedence: CLI args (`--token`, `--initial-user`, `--users-file`) > env/.env > defaults.
- httpx logging is lowered to WARNING because its URLs contain the bot token.
- Commands that need async chat I/O (`/social`, later `/url`, `/google`) are flagged `needsChat=True` and are
  implemented in `bot.py` (`MakeSocialCommandCallback` etc.); their sync handler is only a usage fallback.
  They use `registry.Authorize` for the role check and `inGroups=True` makes them work in groups too.
  `/social` and the implicit link handler share `ProcessLinks`.
- New commands: write `Handler(context) -> str`, register in `BuildRegistry` with help lines and `minRole`.

- Link cleaning (`links.py`) is an ALLOWLIST: keep the path (or for old Facebook forms only v / fbid /
  story_fbid+id), drop every other parameter and fragment; never switch to a blocklist of tracking params.
- Social downloads: downloaders return a `RemotePost` (URLs only); `MediaFetcher` downloads files.
  gallery-dl runs as a subprocess with `-j`; cookies go via a temp 0600 config, never argv.
- Auth is browser-session only (Instagram `sessionid`, Twitter `auth_token`, Facebook cookies file). Username/
  password + 2FA support was removed on purpose (owner confirmed sessions work; gallery-dl dropped Twitter
  password login; Facebook/Threads never had it).
- Videos are sent with width/height/duration from `videoinfo.py`; without them Telegram guesses the shape
  and shows landscape videos wrongly. Rotation matrix is honored. Fixtures in `tests/data` made with ffmpeg.
- gallery-dl and yt-dlp do NOT support Threads, hence the custom reader (fragile, anonymous only). Threads and
  Facebook both return a stripped page (no post data) to Chrome-style/unknown headers: use the Firefox-style
  `PAGE_HEADERS` (no zstd, httpx can't decode it). Threads thread parts live under
  `text_post_app_info.self_thread.posts.edges[].node` of a PARTIAL copy of the post (`{id, text_post_app_info}`,
  matched by `id`), ordered by `self_thread.post_ids`.
- Facebook: gallery-dl (media) + `facebook.py` page reader (sharer's own author/text; gallery-dl follows shares
  to the original and has no option for this).
- Strangers get `store.DeniedText()` ("Please ask my owner @x ...") everywhere; owner = user with the owner
  role (the initial user). Usernames are refreshed on contact.
- Groups only work reliably with BotFather privacy mode DISABLED (+ re-add the bot); logged at startup via
  `can_read_all_group_messages`. Do not claim @mentions are delivered under privacy mode (unverified).
- Group handling: react only to @mention (+ replies to a message when the reply mentions the bot).
  Updates are processed concurrently (downloads are slow).

- Reliability: `main.py` runs `RunForever(RunOnce)`; everything holding an event loop (httpx clients, the PTB
  Application) is rebuilt per attempt. Fatal = `InvalidToken` only. Unexpected failures are reported via
  `Notifier.ReportFailure`; expected `DownloadError`s are not (only `UnexpectedDownloadError`). Process-level
  restarts are left to Docker/systemd.
- `/restart` = clean stop + `os.execve` of `sys.orig_argv` with the pre-`.env` environment (same pid, reloads
  code/.env; reason passed via internal `MINIONBOT_RESTART_REASON`). `/shutdown` = clean stop, exit 0. Both are
  `minRole=Role.OWNER` commands. Docker/systemd must use
  restart-on-failure, not always/unless-stopped. `minionbot/control.py` is the command -> main signal.
- PTB prints the bot token in some exceptions: `RedactSecrets` installs a log filter; keep it.

## Verification status
Tests use mocks/fixtures. Checked against real Telegram with a FAKE token only: invalid token exits fatally, two
run_polling calls in one process work. Owner notifications, /restart, /shutdown and crash/restart are NOT verified live (re-exec itself was checked
with a probe script, as script and as `-m`). Verified live by the owner: Instagram (sessionid) and Twitter (auth_token) downloads.
Verified live by the agent (read-only, anonymous): Facebook share post and a Threads 8-part text thread.
NOT verified: Threads posts with images/video, Facebook video posts, the video aspect-ratio fix in real Telegram. The MP4 reader was checked
against ffprobe on generated files.

## Not yet
Dockerfile/compose, real-Telegram verification.
