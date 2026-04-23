# RateBridge — Handoff Document

**Date**: 2026-04-23
**Owner**: Shmuel (shh92533@gmail.com)
**Use**: Self-contained briefing for continuing work on the project.

---

## 1. What the project is

**RateBridge** is a **delta-neutral funding-rate arbitrage bot** running live
on a VPS in Tokyo. It takes opposing positions across crypto exchanges
(long on one, short on another) to capture funding-rate differentials
while staying market-neutral.

- **Code**: Python 3.11 (asyncio + uvloop) + React/TypeScript dashboard
- **Exchanges connected (5 of 7 supported)**: Binance, Bybit, KuCoin, Gate.io, Bitget
- **Capital**: ~$129 spread across exchanges (intentionally small for now)
- **Mode**: LIVE trading (`PAPER_TRADING=false`, `DRY_RUN=false`)
- **Trades so far**: 1 (CHIP, +$0.18 = +0.59% net, `basis_recovery_exit`)

---

## 2. Infrastructure

### Repo
- GitHub: `https://github.com/Shmuel18/Arbitrage-` (note the trailing dash)
- **Primary branch**: `main` (all latest code lives here)
- Legacy: `v3-live` is pushed in sync with main (same commits)
- Local working copy: `C:\Users\shh92\Documents\Arbitrage` (Windows)

### Server (Vultr Tokyo)
- **IP**: `149.28.23.129`
- **Domain**: `ratebridge.live` (Let's Encrypt SSL, auto-renew via certbot)
- **OS**: Ubuntu 22.04 LTS
- **Specs**: 2 vCPU (Intel 3.8GHz) / 4 GB RAM / 128 GB NVMe
- **SSH**: `ssh root@149.28.23.129` (key at `C:\Users\shh92\.ssh\id_ed25519`)
- **Project path**: `/opt/ratebridge`
- **Branch checked out on server**: `main`

### Stack (hybrid deployment)
**Docker containers** (`/opt/ratebridge/docker-compose.yml`):
- `trinity-bot` — Python bot, exposes `127.0.0.1:8000`
- `trinity-redis` — Redis 7, exposes `127.0.0.1:6379`
- `trinity-prometheus` — metrics DB, `127.0.0.1:9090`
- `trinity-grafana` — dashboard UI, `127.0.0.1:3000`
- `trinity-node-exporter` — host metrics (internal)
- `trinity-cadvisor` — container metrics (internal)
- `trinity-redis-exporter` — redis metrics (internal)

**Native on host** (kept native to avoid SSL migration risk):
- `nginx` — reverse proxy, serves frontend static + proxies `/api/*` `/ws/*` `/grafana/*`
- `certbot` — SSL renewal via systemd timer
- `chrony` — time sync (NICT Japan, sub-microsecond)
- `fail2ban`, `ufw` (22/80/443 only open externally)

### .env (`/opt/ratebridge/.env`, chmod 600)
Must contain (names only, values omitted):
- Core: `ENVIRONMENT`, `LOG_LEVEL`, `PAPER_TRADING`, `DRY_RUN`, `LIVE_CONFIRMED`
- Redis: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`
- Exchanges (set): `BINANCE_*`, `BYBIT_*`, `KUCOIN_*`, `GATEIO_*`, `BITGET_*`
- Exchanges (empty): `OKX_*`, `KRAKEN_*` (user chose to skip)
- Auth: `ADMIN_TOKEN` (96-char hex)
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MINI_APP_URL=https://ratebridge.live`
- AI: `GEMINI_API_KEY`, `GROQ_API_KEY`, `AI_PROVIDER=auto`
- Grafana: `GRAFANA_ADMIN_PASSWORD`

### Frontend (`/opt/ratebridge/frontend/.env.local`)
`VITE_ADMIN_TOKEN`, `VITE_READ_TOKEN`, `VITE_WS_TOKEN`, `VITE_COMMAND_TOKEN`, etc.
All set to the same value as backend `ADMIN_TOKEN`.

---

## 3. What has been delivered

### Phase 0 — Deployment & hardening
- [x] Vultr VPS provisioned (Tokyo, High-Frequency Intel)
- [x] Ubuntu 22.04 + Python 3.11 (deadsnakes PPA)
- [x] Docker + Docker Compose installed via get.docker.com
- [x] Node.js 20 via NodeSource
- [x] SSH key-only auth, password login disabled
- [x] UFW firewall (22/80/443)
- [x] fail2ban for SSH brute-force protection
- [x] `unattended-upgrades` for auto security patches
- [x] `chrony` time sync (Asia-Pacific NTP, <1ms to NICT Japan)
- [x] Kernel tuning: TCP BBR, swappiness=10, larger socket buffers
- [x] File descriptor limits raised to 65536
- [x] Let's Encrypt SSL on `ratebridge.live` + `www.ratebridge.live`
- [x] Domain purchased via Namecheap, A records configured

### Phase 1 — Docker migration (hybrid)
- [x] `Dockerfile` (multi-stage: frontend build + Python runtime)
- [x] Bot + Redis containerized, nginx stays native
- [x] `docker-compose.yml` uses `env_file: .env`
- [x] `API_BIND_HOST=0.0.0.0` in container, nginx proxies on host
- [x] systemd service removed, Docker restart policy handles resilience
- [x] Healthcheck on `/openapi.json` (bypasses auth requirement)

### Phase 2 — Monitoring
- [x] Prometheus + Grafana + node_exporter + cAdvisor + redis_exporter running
- [x] Dashboard JSON in `monitoring/grafana/dashboards/ratebridge-overview.json`
- [x] Prometheus scrape config at `monitoring/prometheus/prometheus.yml`
- [ ] **Grafana NOT reachable via HTTPS yet** — `/grafana/` nginx location block exists only on HTTP (port 80) `server` block, missing from HTTPS (port 443) block. See Pending #1.

### Phase 3 — AI Assistant
- [x] Multi-provider architecture: Groq (primary, free) → Gemini (fallback, free) → Anthropic (paid, optional)
- [x] Auto-fallback on `429 quota` and `tool_use_failed` errors
- [x] Tool use: `get_status`, `get_balances`, `get_open_positions`, `get_recent_trades`, `get_top_opportunities`, `get_pnl_summary`
- [x] `int|string` schema to tolerate Llama's stringified numbers
- [x] Conversation history: Redis per Telegram chat_id, state per browser session
- [x] `/ask <q>` command + reply-to-bot natural questions in Telegram
- [x] `POST /api/ai/chat` endpoint (body: `{question, lang, history}`)
- [x] Floating chat widget on dashboard (`AIChatWidget.tsx` + `ai-chat.css`)
- [x] Telegram Mini App working (opens dashboard inside Telegram)

### Nice-to-have already done
- [x] Daily-summary script in Telegram (`/opt/ratebridge/src/notifications/daily_summary.py`)
- [x] Healthcheck cron (`ops/healthcheck.sh`) pings Telegram on state changes
- [x] Operational scripts in `/opt/ratebridge/ops/`: `status.sh`, `logs.sh`, `deploy.sh`, `rotate_token.sh`
- [x] Aliases in `/etc/profile.d/ratebridge.sh`: `rb-status`, `rb-logs`, `rb-deploy`, `rb-restart`, `rb-edit-env`
- [x] Holiday banner component (`HolidayBanner.tsx`) — commented-out in `App.tsx`, ready to re-enable

---

## 4. Pending tasks (priority order)

### P1 — Finish what's started (small & fast)

1. **Fix Grafana `/grafana/` route on HTTPS server block.**
   - Current state: `ops/setup_grafana_route.sh` only inserted the `location /grafana/` block before `# Main SPA route` marker — but that marker only exists in the HTTP (port 80) server block, NOT the HTTPS (port 443) block auto-generated by certbot.
   - File: `/etc/nginx/sites-available/ratebridge`
   - Need: add the same two `location /grafana/` blocks inside the `listen 443 ssl` server block (see lines 84 onward).
   - Then: `nginx -t && systemctl reload nginx`
   - Verify: `curl -sI https://ratebridge.live/grafana/` returns 302 (redirect to login) not 200 with RateBridge HTML.
   - Then tell the user the Grafana admin password (stored in `.env` as `GRAFANA_ADMIN_PASSWORD`).

2. **Verify AI chat works end-to-end after the latest fixes.**
   - Last deployed commit added conversation history support.
   - Ask user to test in Telegram: "כמה הרווחתי היום?" → then "למה?" (follow-up) — should be coherent.
   - Also test the web widget at `https://ratebridge.live` (floating 🤖 bubble).

3. **Rotate Gate.io API key** (exposed in an earlier chat via terminal `env` dump).
   - User action: regenerate at Gate.io dashboard, update IP whitelist to `149.28.23.129`, no withdraw permission.
   - Then user updates `/opt/ratebridge/.env` with `GATEIO_API_KEY` / `GATEIO_API_SECRET`.
   - Run: `docker compose up -d --force-recreate bot`.

### P2 — Phase 4: Backtesting framework (bigger project)

Was on the original roadmap, never started. Goal: let the user test strategy parameter changes against historical funding-rate data before deploying to live.

**Suggested architecture**:
- `scripts/fetch_historical_data.py` — pulls funding-rate history per exchange+symbol using CCXT's `fetch_funding_rate_history`. Save to Parquet in `data/history/<exchange>/<symbol>.parquet`.
- `src/backtest/engine.py` — event-driven replay: iterates funding-rate events in timestamp order, runs the current entry/exit logic, records simulated trades.
- `src/backtest/runner.py` — CLI entry point: `python -m src.backtest.runner --from 2025-01-01 --to 2026-04-22 --symbol BTC/USDT:USDT --pair binance_bybit`.
- `src/backtest/report.py` — metrics: equity curve, Sharpe, max drawdown, win rate, per-trade P&L distribution.
- Reuse `src/execution/*` scoring/classification logic (NUTCRACKER vs CHERRY) where possible — resist duplicating.
- Output: JSON + HTML report + optional Grafana data source.

**Must decide up front**:
- Data source for historical rates: CCXT only (simplest, but some exchanges have limited history) vs. commercial (CryptoQuant etc.).
- Fidelity of simulation: just funding P&L, or also model entry/exit slippage + fees based on historical order book snapshots?
- How to handle symbol listings (a symbol may not exist on all exchanges for the whole range).

### P3 — Observability enhancements

4. **Add `/metrics` endpoint in the bot.**
   - `pip install prometheus-client` (already in image? check).
   - Expose Prometheus-format metrics: `ratebridge_active_positions`, `ratebridge_scan_duration_seconds`, `ratebridge_opportunity_net_pct`, `ratebridge_ws_staleness_ms`, exchange-specific latency histograms.
   - Prometheus scrape config already has `trinity_bot` job pointing at `bot:8000/metrics` — currently returns 404.

5. **Bot-specific Grafana dashboard.**
   - Panels: open positions, cumulative P&L, trades/day, win rate, average hedge gap, funding collections, per-exchange balance trend.
   - Save as `monitoring/grafana/dashboards/ratebridge-trading.json` — auto-provisioned.

6. **Docker container log rotation.**
   - Add `logging:` section to each service in docker-compose.yml with `json-file` driver, `max-size: 10m`, `max-file: 5`.
   - Without this, `/var/lib/docker/containers/*/*-json.log` will grow unbounded.

### P4 — Operational polish

7. **Full Docker migration** — move nginx + certbot into Docker.
   - Pros: everything in one stack, simpler snapshotting, easier DR.
   - Cons: requires SSL migration (cert volumes into container), risk of downtime.
   - Current hybrid works fine; this is optional.

8. **Backup automation** — nightly `redis-cli --rdb` → S3 or local rotating backup.
   - Redis has `redis_data` named volume; snapshot via `docker run --rm -v ratebridge_redis_data:/data busybox tar czf /backup/redis-$(date +%F).tar.gz -C /data .`.
   - Also backup `/opt/ratebridge/.env` (encrypted).

9. **CI/CD via GitHub Actions**
   - On push to `main`: SSH into server, `git pull`, `docker compose build bot`, `docker compose up -d --force-recreate bot`.
   - Store server SSH key as GitHub secret.

### P5 — Nice-to-have (roadmap)

10. **Advanced Telegram alerts** — separate channels for info/warnings/critical; P&L-threshold alerts; drawdown alerts.
11. **Mobile UI pass** — the dashboard works on mobile but panels aren't tuned.
12. **Multi-wallet / sub-account** — run the same strategy across multiple accounts on the same bot.
13. **Strategy parameter UI** — change `max_position_size_usd`, `min_net_bps`, etc. from dashboard instead of editing `config.yaml`.

---

## 5. Quick reference for a new Claude session

### Daily commands (after SSH-ing in)
```bash
# Full system status
/opt/ratebridge/ops/status.sh

# Bot logs (colorized JSON)
docker logs -f trinity-bot

# Redis
docker exec trinity-redis redis-cli ping

# Restart bot after code change
cd /opt/ratebridge && git pull origin main && \
  docker compose build bot && \
  docker compose up -d --force-recreate bot

# Rebuild frontend only
cd /opt/ratebridge/frontend && npm run build
```

### User context
- **Language**: Hebrew primary, English fine
- **OS on their computer**: Windows 11, uses PowerShell for SSH
- **Known PowerShell quirks**: escaping `$(...)`, parentheses in echo, heredocs through SSH are unreliable — prefer writing scripts locally then `scp` + `bash script.sh`
- **User is hands-on** but new to server administration; prefers step-by-step instructions over "here's a huge script, run it"

### Security hygiene (already enforced)
- `.env` is `chmod 600`, root-owned
- API tokens never logged at INFO
- SSH key-only (password disabled)
- Redis bound to `127.0.0.1` only (Docker port mapping enforces)
- FastAPI bound to `127.0.0.1` via `API_BIND_HOST` env (container-side is `0.0.0.0` but Docker maps only to host localhost)
- Exchange keys have IP whitelist to `149.28.23.129` only, no withdraw permission

### Pitfalls learned the hard way
- `docker compose restart` does NOT reload `env_file`. Use `up -d --force-recreate`.
- `rotate_token.sh` and other old scripts may still reference `systemctl restart ratebridge` — systemd unit was removed, they need `docker compose up -d --force-recreate bot`.
- When the user crashes/refreshes the browser with stale WebSocket, the `WS RECONNECTING` badge appears. `useWsFeed.ts` has a 30s watchdog that force-reconnects — should self-heal within 10s.
- PowerShell + SSH with complex quoting: use `scp` of a local `.sh` file then `ssh 'sed -i "s/\r$//" /tmp/script.sh && bash /tmp/script.sh'`.

---

## 6. How to continue

1. Read this file top to bottom.
2. Check current state:
   ```
   ssh root@149.28.23.129
   /opt/ratebridge/ops/status.sh
   docker ps
   git -C /opt/ratebridge log --oneline -5
   ```
3. Pick a pending task from section 4, starting with P1.
4. When committing, push to both `main` and `v3-live`:
   ```
   git push origin main
   git push origin main:v3-live
   ```
5. Deploy on server: `git pull origin main --ff-only && docker compose build bot && docker compose up -d --force-recreate bot`.

Good luck!
