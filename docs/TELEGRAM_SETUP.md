# Telegram notifications + Mini App

RateBridge can push every trade event, daily summary, and status check to
your Telegram — and the full dashboard can be opened as a Mini App from
inside Telegram.

This guide walks through the three moving parts:
1. **Bot** → `@BotFather` registration
2. **Push alerts** → three env vars and a restart
3. **Mini App** → deploy the built frontend behind HTTPS

You can stop after part 2 for notifications only; part 3 is independent.

---

## 1. Create the bot (5 minutes)

1. In Telegram, open a chat with [`@BotFather`](https://t.me/BotFather).
2. Send `/newbot`. Pick a display name (e.g. "RateBridge alerts") and a
   username ending in `bot` (e.g. `ratebridge_alerts_bot`).
3. BotFather replies with an HTTP API token like
   `123456789:AAH3...`. **Save it.**
4. In that same chat, send `/setdescription` and describe the bot; optional
   but nice.
5. Open a chat with YOUR bot (search by username) and send `/start`.
6. Find your `chat_id`:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
   ```
   Look for `"chat":{"id":<CHAT_ID>,...}`. That's the number to use.

---

## 2. Enable push notifications

Edit `.env` (copy from `.env.example` if you haven't yet):

```env
TELEGRAM_BOT_TOKEN=123456789:AAH3...
TELEGRAM_CHAT_ID=1234567890
# defaults shown — flip to false to mute any category
TELEGRAM_NOTIFY_OPEN=true
TELEGRAM_NOTIFY_CLOSE=true
TELEGRAM_NOTIFY_SUMMARY=true
# daily digest time (local to tz below)
TELEGRAM_SUMMARY_HOUR=23
TELEGRAM_SUMMARY_MINUTE=55
TELEGRAM_SUMMARY_TZ=Asia/Jerusalem
```

Restart the bot:

```bash
# Whatever you use to run it — e.g.
python main.py
```

On startup the bot sends you a "RateBridge online" ping. If you don't see
it within 5 seconds, the token or chat ID is wrong; logs print a `⚠️
Telegram self-test FAILED` line.

### Commands the bot understands

| Command | Effect |
|---------|--------|
| `/start` | Greeting + prints your Telegram user ID |
| `/status` | One-shot: running/stopped, positions, today's PnL, win rate |
| `/menu` | Button to open the Mini App (requires step 3 below) |

### Optional — lock to your Telegram user ID only

By default anyone who knows the bot username can run `/status`. After
`/start`, copy your own user ID from the reply and add to `.env`:

```env
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Restart. Now only you (and anyone you add, comma-separated) can issue
commands or open the Mini App.

---

## 3. Mini App (optional)

The Mini App lets you open the full dashboard inside Telegram — with
the same real-time data and controls as the browser view.

### 3a. Build the dashboard

```bash
cd frontend
npm install
npm run build
# → produces frontend/build/ with index.html + assets
```

### 3b. Host behind HTTPS

The Mini App SDK requires an HTTPS URL. Pick one:

#### Option A — Cloudflare Pages (recommended for production)

1. Push the `frontend/build/` output to a git branch (or connect the whole
   repo and set output directory to `frontend/build`).
2. Create a Pages project, pick the repo, set build command
   `cd frontend && npm install && npm run build` and output dir
   `frontend/build`.
3. Pages assigns a URL like `https://ratebridge.pages.dev`. Done.
4. Set `VITE_API_BASE=https://api.your-domain.tld` at build time if the
   API runs on a different host from the static build. Default same-origin
   works if you reverse-proxy `/api` to the bot's port 8000.

#### Option B — Vercel

Same as Cloudflare; point at `frontend/` as the project root.

#### Option C — Local tunnel (quick demo, not production)

```bash
# one terminal: keep the dev server running
cd frontend && npm run dev

# second terminal: expose it
cloudflared tunnel --url http://localhost:3000
# → prints a random https://xxx.trycloudflare.com URL
```

The URL rotates every run — fine for testing, not for a permanent menu
button.

#### Option D — Self-host

`nginx` serving `frontend/build/` with a reverse-proxy block for `/api`
and `/ws` pointing to your bot's port 8000. You own the domain + certs.

### 3c. Tell the bot where the Mini App lives

```env
TELEGRAM_MINI_APP_URL=https://ratebridge.pages.dev
```

Restart the bot. Now `/menu` in Telegram shows a button that opens the
dashboard directly inside the app.

### 3d. One-time: set the chat menu button (optional polish)

Makes a ☰ button appear at the bottom-left of every chat with your bot.

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d '{
    "menu_button": {
      "type": "web_app",
      "text": "Dashboard",
      "web_app": { "url": "https://ratebridge.pages.dev" }
    }
  }'
```

---

## Security notes

* **Bot token never lands in the repo.** It lives in `.env` (gitignored)
  and is loaded as `SecretStr`. Error messages mask it with `***` if it
  ever appears in a response body.
* **initData is cryptographically verified** server-side (`api/telegram_auth.py`).
  The backend trusts the Telegram user ID only after the HMAC-SHA256
  signature validates and `auth_date` is within 24h — replay attacks and
  forged payloads are rejected.
* **Dual auth path.** The dashboard accepts **either** the existing
  `X-Read-Token` (desktop browsers) **or** a valid `X-Telegram-Init-Data`
  (Mini App). You don't have to pick one.
* **Allowlist** via `TELEGRAM_ALLOWED_USER_IDS` restricts which Telegram
  users can run commands or open the Mini App, even if they somehow
  obtained a signed initData. Leave empty to accept anyone who can reach
  the bot (fine while you're the only user).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No "online" ping at startup | Token or chat ID wrong | Check `getUpdates` again |
| "Telegram rate-limited" in logs | Sending > 1/sec to same chat | Normal during trade storms; messages dropped silently |
| `/menu` says "Mini App is not configured" | `TELEGRAM_MINI_APP_URL` not set | Set it, restart |
| Mini App opens but every API call 401s | `X-Telegram-Init-Data` not validating | Check bot token in env matches the one in BotFather |
| Mini App 403s with "User not in allowlist" | Your user ID isn't whitelisted | Remove `TELEGRAM_ALLOWED_USER_IDS` or add yours |
| No daily summary at 23:55 | Timezone mismatch | Check `TELEGRAM_SUMMARY_TZ`; the bot uses IANA names (e.g. `America/New_York`) |

## Message examples

```
🟢 Trade opened
Trade opened: abc-123 ENJ/USDT:USDT L=kucoin S=bitget net=0.8130%
ENJ/USDT:USDT
```

```
✅ Trade closed
Trade closed: abc-123 ENJ/USDT:USDT pnl=+$0.53 hold=46m
ENJ/USDT:USDT
```

```
📊 Daily summary
Trades: 14  (11W / 3L)
Total PnL: +$7.85
Win rate: 78.6%
Best: ENJ/USDT:USDT +$1.81
Worst: METIS/USDT:USDT -$0.40
Top symbols: ENJ +$2.34  ·  ZETA +$1.21  ·  SAGA +$0.89
```
