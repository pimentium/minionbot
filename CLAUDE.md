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
- `minionbot/ai/markdown.py`: Gemini Markdown -> Telegram HTML (`ToHtml`, balance-checked, escapes everything) and
  `SplitMarkdown` (fence-aware chunks). `/gemini` = `GeminiClient.Ask` + `ReplyWithMarkdown` in `bot.py`; one
  `GeminiClient` instance is shared by translation and `/gemini` (created in `main.RunOnce`).
- `minionbot/translate/`: `/rus` `/eng`. `gemini.py` (client: header key, JSON array in/out, strict length check,
  batch halving, retries), `articles.py` (block-wise translation that keeps inline markup; only the source's own
  hrefs survive), `language.py` (skip pages already in the target language).
- `minionbot/search/`: `/google`. `serpapi.py` (SerpApi client + tolerant parsing of organic_results and ai_overview;
  second request with `engine=google_ai_overview&page_token=` when only a token is returned), `format.py` (Telegram
  HTML, everything escaped, split on line boundaries), `models.py`.
- `minionbot/web/`: `/url` and implicit web links. `urls.py` (find/clean non-social URLs; blocklist of tracking params),
  `safety.py` (SSRF: public IPs only, port allowlist), `fetcher.py` (manual redirects re-checked per hop, peer
  address re-checked, size/type/time limits), `reader.py` (trafilatura -> Telegraph nodes, paywall markers),
  `archive.py` (Wayback only), `telegraph.py` (publish + token file), `service.py` (orchestration).

## Decisions
- Users identified by numeric ID or @username; username entries bind to the ID on first contact.
- Roles replace permissions: `Role` IntEnum UNKNOWN < USER < ADMIN < OWNER (in `users.py`), each includes the
  lower ones. A `Command` has `minRole`; social link handling needs `SOCIAL_LINKS_MIN_ROLE` (USER). OWNER is
  assigned only by `UserStore.EnsureInitialUser` (the --initial-user at start; the previous owner becomes
  admin) and can't be assigned/changed/removed by commands. Nobody can change their own role. `/whoami` for
  UNKNOWN keeps the short original format. Old files migrate without gaining privileges (owner flag -> owner,
  explicit "admin" permission -> admin, else user). `perms=` and permission strings no longer exist.
- Storage: JSON file (chosen over SQLite by owner). Corrupt file => refuse to start, never overwrite.
- Unknown users: `DeniedText` (names the owner, mentions `/friend`) for everything but `/whoami` and `/friend`. Private chats only.
- Config precedence: CLI args (`--token`, `--initial-user`, `--users-file`) > env/.env > defaults.
- httpx logging is lowered to WARNING because its URLs contain the bot token.
- Commands that need async chat I/O (`/social`, later `/url`, `/google`) are flagged `needsChat=True` and are
  implemented in `bot.py` (`MakeSocialCommandCallback` etc.); their sync handler is only a usage fallback.
  They use `registry.Authorize` for the role check and `inGroups=True` makes them work in groups too.
  `/social` and the implicit link handler share `ProcessLinks`.
- `@bot /cmd args` (mention first) is parsed by `ParseMentionCommand` in the catch-all `MakeMessageCallback` and
  dispatched to the same callbacks (`ArgsContext` supplies `args`); only commands with `needsChat and inGroups` are
  reachable this way, never private-only ones. Link-taking commands fall back to the replied-to message
  (`CommandSources`). Without this a mention + `/rus` silently became plain `/url` (found by the owner).
- `/help` is Telegram HTML (`Command.html=True`; the adapter's `ReplyWithHtml` splits between entries at <=3900 chars and
  falls back to tag-free text on `BadRequest`). `Command.help` holds `HelpEntry(syntax, summary, details)`; sections by
  `minRole`, least restricted first (`ROLE_TITLES`). New commands must describe their cases (reply, link, text, media) in
  `details`; everything is escaped.
- `/friend` (`friends.py`, `MakeFriendCommandCallback`, `MakeFriendDecisionCallback`): stranger -> message to the owner with
  inline Approve/Deny (`friend:approve|deny:<id>`) -> `store.Add(role USER)` and both sides told. Only the OWNER role's press
  counts. State is in-memory (`FriendRequests`: pending + 24 h cooldown after a denial); after a restart a button press still
  works with the ID only. Works in groups too (`inGroups=True`): the request remembers chat+message, the answer is a reply there, DM fallback. Needs the owner's numeric ID (he must have contacted the bot). Not verified against real Telegram.
- New commands: write `Handler(context) -> str`, register in `BuildRegistry` with help lines and `minRole`.

- Link cleaning (`links.py`) is an ALLOWLIST: keep the path (or for old Facebook forms only v / fbid /
  story_fbid+id), drop every other parameter and fragment; never switch to a blocklist of tracking params.
- Social downloads: downloaders return a `RemotePost` (URLs only); `MediaFetcher` downloads files.
  gallery-dl runs as a subprocess with `-j`; cookies go via a temp 0600 config, never argv.
- Auth is browser-session only (Instagram `sessionid`, Twitter `auth_token`, Facebook cookies file). Username/
  password + 2FA support was removed on purpose (owner confirmed sessions work; gallery-dl dropped Twitter
  password login; Facebook/Threads never had it).
- `/url`: reader mode via trafilatura; result published to Telegraph (public pages, owner chose this for Instant
  View); teaser pages fall back to Internet Archive copies (owner chose archive fallback; only after paywall markers +
  little text). archive.ph was removed: it drops TLS handshakes from non-browser clients, cannot work for a script,
  and reaching it would need browser-fingerprint spoofing, which we do not do.
  Refused pages (HttpStatusError.refused: 401/402/403/429/451 or detected bot protection) also try the archives;
  404/5xx never do. Do not add UA/crawler impersonation or challenge solving (owner chose archives only).
  SSRF protection in `web/safety.py` + `fetcher.py` is mandatory: never fetch user URLs around it. Implicit
  handling: any non-social URL in a private message / mention / mention-reply is treated like `/url`.
- `/rus` `/eng` = `/url` + `TranslateArticle` inside `WebService.Read(url, language)` before the Telegraph page is
  created (same `MakeUrlCommandCallback`, `language` param). Gemini via `generateContent` with only
  `responseMimeType: application/json` (no response schema: its generateContent form is undocumented) and strict
  validation of the answer. Invalid key is HTTP 400 `API_KEY_INVALID` (not 401). Free tier: Google may use the data
  to improve products (owner knows/chose free tier). Translation key/model errors (`TranslationServiceError`)
  notify the owner. Page text is untrusted: prompt says translate only, output is validated, hrefs are whitelisted.
- `/rus` `/eng` without a link translate text (`translate/text.py`, `ProcessText` in `bot.py`): order is link in the command >
  text given to the command (`PromptText`) > link in the replied-to message > its text (`ReplyQuote`). Plain-text reply, same
  already-in-language check; fragments escaped in, tags stripped out. `/url` never translates text. Not verified live.
- `/gemini <prompt>`: raw text after the command (`PromptText`: `rawArgs` after a mention, else the message text minus
  the command word) goes to Gemini with a short chat system instruction; single-turn, no memory. HTML first, plain
  text fallback on `BadRequest`. As a reply it also sends the replied-to text (Telegram quote if selected, else text/
  caption; appended at the END of the prompt, capped at 8000 chars) and its photo/video/animation/video_note/image
  document inline (`GeminiClient.Ask(prompt, media)`, one file, 15 MB cap; media refused with a message, never silently
  dropped). Gemini error wording says "Gemini service" (shared with translation).
- `/google`: SerpApi chosen by the owner (scraping Google is blocked and against its terms; AI overview is not in the
  HTML scripts receive). The key is in the URL query, so httpx errors/URLs must never be logged or shown: the client
  maps errors to its own messages and scrubs the key; key is in the redaction secrets. Key/quota errors
  (`SearchServiceError`) notify the owner. Provider-specific code stays in `serpapi.py`.
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
Verified live by the agent (read-only, anonymous): Gemini invalid-key answer (HTTP 400 API_KEY_INVALID) and a real
23k-char page through the translation round trip with a pretend translator (links/headings/lists intact); SerpApi error format with an invalid key (HTTP 401 + JSON error); /url fetch+reader on gnu.org, python.org, example.com; SSRF
blocks incl. localtest.me; Wayback lookup; Facebook share post and a Threads 8-part text thread.
NOT verified: any real Gemini call (translation, /gemini incl. reply text/media, whose inlineData request shape follows Google's docs) (no key during development; prompt quality, the free-tier limits and
the default model name rest on Google's docs), a real /google search (no SerpApi key during development; the AI overview and page_token flows rely on
SerpApi's documentation, the second response format is not documented), publishing to Telegraph and Instant View in real Telegram (deliberately not published during
development), Threads posts with images/video, Facebook video posts, the video aspect-ratio fix in real Telegram. The MP4 reader was checked
against ffprobe on generated files.

## Deployment
- `.github/workflows/deploy.yml`: on push to `main` run pytest, then pack `minionbot main.py requirements.txt deps`
  (`deps/` = `pip install --target` with `--only-binary=:all:` for the server's Python and glibc, compiled with
  `unchecked-hash` pycs because the release is read-only on the server), then upload `release.tar.gz` to
  `DEPLOY_URL/deploy` (signed with `DEPLOY_SECRET`) and poll `DEPLOY_URL/status`. The server side (container, Python
  image, webhook, secrets) is the `minionbot` role in the `minions` repo; there is deliberately no Dockerfile here.
- A release may contain only regular files and directories (no symlinks, no hard links: `--hard-dereference`) and only
  the top-level names `minionbot`, `main.py`, `deps`, `requirements.txt`; it must contain `main.py`, `minionbot/main.py`
  and `deps/telegram/__init__.py`. A new top-level name needs the same change in `minions` (`selfupdating_app_release_top`).
- The bot is started as `python main.py` in the release directory with `PYTHONPATH=deps`. Everything it writes goes to
  `/state` (`USERS_FILE`); the release directory is read-only. Keep new files/state under `USERS_FILE`'s directory.
- `READY_FILE` (env only): `minionbot/ready.py` writes it in `PostInit` after the start-up notification; the deployment
  health check depends on it, so keep that call in `MakePostInit`. `/shutdown` (exit 0) keeps the bot stopped on the server
  until the next release or a container restart; any other exit is restarted by the container's supervisor.
- `tests/data` (mp4 fixtures) must be committed: `.gitignore` ignores only the top-level `/data/`.
- Python in CI (`PYTHON_VERSION`) = `minionbot_python` in `minions`; dependencies need prebuilt cp-wheels for it.
- Verified against the fake Telegram API and a real container supervisor only (see the `minions` repo); NOT verified: the
  workflow on GitHub, the container in Docker, a real deployment.

## Not yet
Real-Telegram verification.
