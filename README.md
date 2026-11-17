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
  and restart. The initial user is created on start if missing and is the bot's owner.
- `USERS_FILE` (optional): defaults to `data/users.json`. Keep it on a volume when running in Docker.

Command-line arguments override the environment / `.env`:

```bash
.venv/bin/python main.py --token <BOT_TOKEN> --initial-user 123456789 [--users-file data/users.json]
```

Note: a token passed on the command line is visible in `ps` output and shell history; prefer `.env`
or environment variables on shared machines.

All config can also be set via environment variables, so Docker works with `--env-file .env` and a mounted `data/`.

## Commands

| Command | Lowest role | What |
|---|---|---|
| `/friend` | unknown | ask my owner for access (also works in groups, and as `@BotName /friend`; the answer is posted there as a reply): the owner gets your name, @username and ID with **Approve** / **Deny** buttons; on approval you are added with the `user` role, on denial you are told the request was denied and may ask again after 24 hours. Pending requests are kept in memory only (a restart forgets them; asking again works). The owner must have written to the bot at least once |
| `/whoami` | unknown | show your Telegram ID and username; users also see their role and permissions |
| `/start`, `/help` | user | list the commands available to your role, in sections by role (least restricted first). Each command is on its own line with its description quoted below it: a one-line summary, then bullet points for the special cases (replies, links, text, media, groups). Tap a command to copy it |
| `/social <link>` | user | download a post from Instagram, Facebook, Twitter/X or Threads; same result as sending the bare link; also works in groups |
| `/url <link>` | user | read a web page in reader mode and send it as an Instant View article (see "Web pages"); also works in groups |
| `/rus <link\|text>`, `/eng <link\|text>` | user | like `/url`, but the page is translated to Russian / English with Gemini first; without a link they translate the given text, or the message you reply to (see "Translation"); also work in groups |
| `/gemini <prompt>` | user | ask Gemini: everything after the command is sent as the prompt, the answer comes back formatted for Telegram; also works in groups |
| `/google <query>` | user | search Google: the AI overview (when Google shows one) and the top results, via SerpApi; also works in groups |
| `/version` | admin | show the running version (commit and date; `development` outside a deployment) |
| `/users list` | admin | list users with their roles |
| `/users add <id\|@username> [user\|admin] [name]` | admin | add a user (role defaults to `user`) |
| `/users update <id\|@username\|name> <user\|admin>` | admin | change a user's role (the role is required) |
| `/users remove <id\|@username\|name>` | admin | remove a user (not yourself, never the owner) |
| `/restart` | owner | restart the bot process (picks up code and `.env` changes) |
| `/shutdown` | owner | stop the bot |
| social network links (no command) | user | sending or mentioning a link downloads the post (see below) |
| other web links (no command) | user | sending or mentioning any other link works like `/url` |

### Commands addressed by mention

Telegram treats only a message that *starts* with `/` as a command. In a chat you will often write the bot's name
first, so the group-capable commands (`/social`, `/url`, `/rus`, `/eng`, `/google`) also work as
`@BotName /rus <link>` or `@BotName /google <query>`, in groups and in private chats. They also look at the message
you reply to when your own message has no link, so replying with `@BotName /rus` (or just `/rus`) to a message with
a link translates that link. Private-only commands (`/users`, `/restart`, ...) are never run this way.

## Roles

`unknown < user < admin < owner`; every role may do everything the lower ones may.

| Role | Who | Adds |
|---|---|---|
| unknown | anyone not in the user list | `/whoami`, `/friend` |
| user | added with `/users add` | `/start`, `/help`, social network links |
| admin | promoted with `/users update` | all `/users` commands |
| owner | **only** the user given at bot start (`--initial-user` / `INITIAL_USER`) | `/restart`, `/shutdown` |

The owner role can't be assigned, changed or removed with commands. If the start user changes, the new one
becomes the owner and the previous owner becomes an admin. Nobody can change their own role. Admins can add,
promote, demote and remove other users, including other admins.

Users added by `@username` are linked to their numeric ID on first message; after that only the ID counts,
so a re-assigned username gives no access. Prefer numeric IDs.

Users files written before roles existed are converted when the bot starts: the old owner becomes the owner,
an explicit `admin` permission becomes the admin role, everyone else becomes `user` (nobody gains privileges;
promote people again with `/users update`).

Strangers get a short refusal that points to the owner and to `/friend`, for everything except `/whoami` and `/friend`. The bot only answers in private chats.

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

## Translation (`/rus`, `/eng`)

`/rus <link>` and `/eng <link>` do what `/url` does (reader mode, Telegraph page, Instant View card, same safety
rules and paywall/archive handling) but translate the article **before** it is published. They need a Gemini API
key (free tier works; create one in Google AI Studio):

```bash
.venv/bin/python main.py ... --gemini-key <GEMINI_API_KEY>      # or GEMINI_API_KEY in the environment / .env
```

Optional: `--gemini-model` / `GEMINI_MODEL` (default `gemini-3.5-flash-lite`, the model Google's docs recommend for
translation; if Google retires it, set another one). Without a key the commands say translation is not configured.

- **Plain text instead of a link:** `/rus some text` or `/eng some text` (also after a mention) translates that text
  and answers with it as ordinary messages (line breaks kept, long results split). Used as a **reply** without a link
  of its own, the command translates the message you replied to (the part you selected with Telegram's quote feature,
  if any, otherwise its text or caption). Order: a link in the command, then text given to the command, then a link in
  the replied-to message, then its text. The same "already in the target language" check applies. The text is sent
  to Gemini as escaped fragments with the usual translate-only instruction and the answer is reduced to plain text.
- **How it translates:** the title and every text block (paragraphs, headings, list items, quotes, captions) are sent
  in batches of up to 6,000 characters; code blocks and images are left alone. Links, bold and italics stay in
  place, and the model cannot add links: only the source's own link addresses survive. A typical long article
  (about 23,000 characters) takes about 6 requests. At most 30,000 characters of a page are translated; a longer page
  is cut at a paragraph boundary and the reply says so. The Telegraph page ends with a "Machine translation
  (Gemini)" note and the source link.
- **Already in the target language:** a page that is already Russian (for `/rus`) or English (for `/eng`) is not sent
  to Gemini; the reply just says so. Other Cyrillic languages such as Ukrainian are still translated.
- **Limits and errors:** the free tier has per-minute and per-day request limits that Google does not publish in its
  docs. The bot retries with backoff when it is rate limited (waiting up to about a minute) and then answers that
  the service is busy; a used-up daily quota is reported as such. A rejected key or an unknown model is reported to
  the user in one line and to the owner as a notification.
- **Privacy:** on the **free tier Google may use what you send to improve its products, and human reviewers may read
  it** (Google's terms say not to send sensitive or confidential data). Only the text of public web pages that
  users ask for is sent, together with nothing else from the chat; a paid (billing-enabled) key is not used this way.
  The key is sent in a request header, never in a URL, and is redacted from logs.
- Machine translation can be wrong or awkward, especially for names, idioms and technical text. The original is one
  tap away in the source link.

### Asking Gemini (`/gemini`)

`/gemini <prompt>` (or `@BotName /gemini <prompt>`) sends **everything after the command** to the same Gemini model
and key as the translation, and returns the answer. Line breaks in the prompt are kept. The prompt may be up to
8,000 characters.

- Gemini answers in Markdown, which Telegram would show as raw `**` and `#` symbols, so the answer is converted to
  Telegram's formatting (bold, italics, code, code blocks, bullet lists, quotes, links; tables become monospaced
  blocks). Everything from the model is escaped, only http(s)/mailto links are kept, and if Telegram rejects the
  formatting the answer is resent as plain text. Long answers are split into several messages; a code block that is
  cut in two is closed and reopened.
- A short instruction is sent with every prompt: answer inside a Telegram chat, stay reasonably concise, answer in
  the language of the question. There is **no memory**: each `/gemini` is a fresh, single question, and replies to the
  bot's earlier answers are not used as context.
- **Replies:** if you send `/gemini` as a reply, the text of the message you replied to (or, if you selected a part
  of it with Telegram's quote feature, just that part) is added **at the end of the prompt** under "The message I am
  replying to:". If that message has a **picture, sticker, video, GIF, video note or image file** (an animated sticker is sent as its still preview), it is sent to Gemini too
  (one file per request, at most 15 MB; other documents are ignored). A reply with no prompt of your own works as well:
  the quoted text alone, or "What is in this?" for a bare picture. Without a reply nothing changes.
- Everything said for `/rus` and `/eng` applies here too: the free-tier **privacy note** (Google may use what you
  send, and human reviewers may read it), the unpublished free-tier limits, retries on rate limits, and owner
  notifications for key or model problems. Anyone with the user role can spend your quota.

## Google search (`/google`)

`/google <query>` replies with Google's **AI overview** (only when Google shows one for that query) followed by the
top organic results (title as a link, domain, snippet). Google blocks scripted searches and forbids scraping, so the
bot uses the [SerpApi](https://serpapi.com) service, which needs an API key (create one on their site):

```bash
.venv/bin/python main.py ... --serpapi-key <SERPAPI_KEY>      # or SERPAPI_KEY in the environment / .env
```

Optional: `--google-language` (default `en`) and `--google-country` (e.g. `us`; default: Google decides).
Google shows AI overviews for English queries and a limited set of countries only, so with another language you
will usually get just the results. Without a key `/google` answers that search is not configured.

- **Privacy and cost:** every query is sent to SerpApi (and, through it, to Google). Each search counts against
  your SerpApi plan; when Google lazy-loads the AI overview the bot makes a second request for it, which may count as
  another search (check your plan). SerpApi caches identical searches for an hour.
- **Problems:** a rejected key or used-up quota is reported to the user in one line and to the owner as a
  notification; timeouts and service errors are only shown to the user.

## Web pages (`/url`)

`/url <link>` – or just sending/mentioning any link that is not an Instagram, Facebook, Twitter/X or Threads
post, with the same private-chat / mention / reply rules as the social links – reads the page in **reader
mode**: the main text and images only, without ads, menus, cookie banners, comments or footers
([trafilatura](https://trafilatura.readthedocs.io/)). The article is published as a **Telegraph** page
(telegra.ph), so Telegram shows its one-tap *Instant View*; the reply is just that link, which Telegram turns into
a card with the title and a text preview (a note appears above it only if just a teaser was available or an
archived copy was used). If Telegraph is unreachable, the text is sent as plain messages (up to about 11,000 characters).

- **Telegraph pages are public** to anyone who has the link, and they republish the article text on a third-party
  service. The account/token is created on first use and kept in `telegraph.json` next to the users file
  (`0600`, git-ignored); give your own with `--telegraph-token` / `TELEGRAPH_TOKEN`.
- **Paywalls:** the page is read as the site serves it. Many "paywalled" articles ship their whole text, which is
  then extracted. If only a teaser comes back (paywall markers plus little text), the bot looks for a copy in the
  Internet Archive (web.archive.org) and says so in the reply; the page address is sent to that service only in
  that case. A hard paywall cannot be bypassed; the reply then says only a teaser was available. The Internet
  Archive only has pages somebody archived, so brand-new articles are rarely found. archive.ph is deliberately not
  used: it drops connections from anything but a browser.
- **Sites that refuse the bot:** many news sites sit behind bot protection (DataDome, Cloudflare, ...) and answer a
  script with HTTP 403 or a challenge page; this is not the paywall itself, and no honest request can pass it.
  The bot recognises these pages ("That site blocks automated access (bot protection)..."), and for refusals
  (HTTP 401/402/403/429/451) it looks for an Internet Archive copy the same way as for teasers. Missing pages (404) and
  server errors never go to an archive. Nothing tries to impersonate a search engine or solve challenges.
- **Safety:** the bot only fetches public `http`/`https` addresses (ports 80, 443, 8080, 8443). Private, loopback,
  link-local and cloud-metadata addresses are refused, also after redirects (checked at every hop) and for names
  that resolve to such addresses (checked again on the real connection). Pages are limited to 4 MB and 20 s,
  only HTML/plain text is accepted.
- **Limits:** no JavaScript is executed, so pages that render their content in the browser (some SPAs, YouTube,
  most social sites) come back empty. Direct links to images, videos and PDFs are refused.

### Link cleaning

Links are reduced to what identifies the post before anything is downloaded, so tracking noise never reaches
the networks: Instagram, Twitter/X and Threads keep only the path (no `igsh`, `rpxt`, `stkn`, `s`, `t`, `xmt`,
...); Facebook keeps only the path for `/user/posts/pfbid...`, `/reel/`, `/share/`, `fb.watch` links and for
the older query-based forms only the identifying parameters (`watch/?v=`, `photo.php?fbid=`,
`permalink.php` / `story.php` with `story_fbid` and `id`). Fragments are dropped, mobile hosts (`m.`, `web.`,
`mobile.`, `mbasic.`) become the normal host, and the same post linked twice with different tracking is
answered once. Other web links lose only well-known tracking parameters (`utm_*`, `fbclid`, `gclid`, `mc_eid`, ...) and
the fragment, because ordinary pages cannot be allow-listed.

### Accounts

Networks block anonymous access, so give the bot a logged-in **browser session**. Username/password login
is not supported (it is flaky, triggers 2FA/challenges, and gallery-dl dropped it for Twitter). All options
are optional; command-line values win over env vars / `.env`.

| Network | Option (env var) | Where to get it |
|---|---|---|
| Instagram | `--instagram-session-id` (`INSTAGRAM_SESSION_ID`) | value of the `sessionid` cookie on instagram.com |
| Twitter/X | `--twitter-auth-token` (`TWITTER_AUTH_TOKEN`) | value of the `auth_token` cookie on x.com |
| Facebook | `--facebook-cookies FILE` (`FACEBOOK_COOKIES_FILE`) | Netscape cookies file exported from your browser |
| Threads | none | public posts are read anonymously (best effort); the author's whole thread is combined |

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
- **/restart and /shutdown:** only the owner role can use them.
  `/restart` stops the bot cleanly and re-executes the same command in the same process id, so new code
  and `.env` changes are picked up; the next start-up message says `Restarted: requested by the owner
  (/restart)`. `/shutdown` stops the bot and exits with code 0; it stays down until someone starts it on
  its host. Both work in private chat only.
- **Hard crashes:** if the whole process dies (out of memory, killed), only an outer supervisor can restart
  it. On the server that is the container's own supervisor (see "Deployment"); with plain Docker or systemd use
  `restart: on-failure` / `Restart=on-failure`. Do **not** use `always` / `unless-stopped` for the bot process itself:
  they would also bring the bot back after `/shutdown` (exit code 0).
- **Logs:** the bot token and session cookies are redacted from all log output (python-telegram-bot itself
  prints the token in some error messages).

## Deployment (self-updating container)

On the server the bot runs in one Docker container that updates itself; nothing else is installed on the host. The
container, its Python and the webhook are set up by the `minionbot` role of the `minions` Ansible repository; this
repository only builds and uploads releases. `.github/workflows/deploy.yml` runs on every push to `main` (and by
hand: Actions, Run workflow):

1. runs the tests; a failure stops everything;
2. packs `minionbot/`, `main.py` and the libraries of `requirements.txt` (downloaded as prebuilt wheels for the
   server's Python, compiled once) into `release.tar.gz`;
3. if `DEPLOY_URL` is set, uploads it with a signed request to the server's webhook, which unpacks it, restarts the bot on
   the new version and **goes back to the previous version** if the new one does not report "ready" (see below) within two
   minutes or keeps crashing. The run waits for the result; red means the server did not switch.

One-time setup in the repository (Settings, Secrets and variables, Actions):

| Kind | Name | Value |
|---|---|---|
| Variable | `DEPLOY_URL` | `https://<host>/hooks/minionbot` (leave unset to only run the tests) |
| Secret | `DEPLOY_SECRET` | shared key of the webhook (64 hex digits, `openssl rand -hex 32`; same value as `minionbot_hook_secret` on the server) |

Things to know:

- **One bot, one place.** A bot token can be used by one running instance only (a second one gets a polling conflict).
  Stop the bot on your computer, or use another bot token for local work, before the server's copy starts.
- **Configuration is not in this repository.** The server passes the options as environment variables (`BOT_TOKEN`,
  `INITIAL_USER`, `GEMINI_API_KEY`, ...) from its encrypted secrets; the user list and the Telegraph token live in
  the container's state directory (`USERS_FILE=/state/users.json`), which survives deployments.
- **Ready file.** If the environment variable `READY_FILE` is set, the bot writes a timestamp there once Telegram accepted
  the token (after the start-up message). The server only keeps a new version when that file appears after the start
  and the bot then stays up for 15 seconds. Without `READY_FILE` nothing is written.
- **Start-up message.** Every deployment restarts the bot, so you get its start-up message in Telegram each time:
  that is also your sign that the new version runs.
- **`/restart`** re-executes the bot in the same process (same release). **`/shutdown`** stops it for good: the server does
  not start it again until the next deployment or a restart of the container (`sudo docker restart minionbot`).
- The Python version in the workflow (`PYTHON_VERSION`) must match `minionbot_python` in the `minions` repository. The
  libraries must have prebuilt Linux x86-64 wheels for it (the workflow does not compile anything).
- Roll back by re-running the workflow of an older commit.

## Tests

```bash
.venv/bin/python -m pytest
```

Tests use mocks only; nothing talks to Telegram.
