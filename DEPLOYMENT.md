# RateBridge Deployment Guide

Production deployment on Vultr Tokyo (Ubuntu 22.04, High-Frequency Intel).

---

## Server Overview

| Item | Value |
|---|---|
| **IP** | 149.28.23.129 |
| **Region** | Tokyo, Japan (NRT) |
| **Spec** | 2 vCPU (3.8 GHz Intel) / 4 GB RAM / 128 GB NVMe |
| **OS** | Ubuntu 22.04 LTS |
| **Python** | 3.11 (deadsnakes PPA) |
| **Node.js** | 20 LTS (NodeSource) |
| **Redis** | 7 (Docker, bound to 127.0.0.1) |

---

## Stack

```
┌─────────────────────────────────────────┐
│ Browser / Telegram Mini App             │
└────────────────┬────────────────────────┘
                 │ HTTPS (future) / HTTP now
                 ▼
┌─────────────────────────────────────────┐
│ Nginx (port 80)                         │
│   ├── / → static /opt/ratebridge/       │
│   │        frontend/build/              │
│   ├── /api/ → 127.0.0.1:8000            │
│   └── /ws/  → 127.0.0.1:8000 (upgrade)  │
└────────────────┬────────────────────────┘
                 │ localhost only
                 ▼
┌─────────────────────────────────────────┐
│ ratebridge.service (systemd)            │
│   └── Python 3.11 + uvloop + FastAPI    │
└────────────────┬────────────────────────┘
                 │ 127.0.0.1:6379
                 ▼
┌─────────────────────────────────────────┐
│ trinity-redis (Docker)                  │
└─────────────────────────────────────────┘
```

---

## Security

- **UFW firewall** — only 22 (SSH), 80 (HTTP), 443 (HTTPS) open
- **fail2ban** — bans IPs after 5 failed SSH attempts for 1 hour
- **SSH** — key-only auth (no passwords)
- **Redis** — bound to `127.0.0.1` only (Docker `-p 127.0.0.1:6379:6379`)
- **FastAPI** — binds to `127.0.0.1:8000` only; nginx proxies external traffic
- **.env** — `chmod 600`, root-owned
- **systemd hardening** — `NoNewPrivileges`, `PrivateTmp`, `ProtectKernel*`
- **unattended-upgrades** — auto-applies Ubuntu security patches (Docker excluded)

---

## Performance tuning

- **TCP BBR** — Google's congestion control (`sysctl net.ipv4.tcp_congestion_control=bbr`)
- **FQ qdisc** — fair queueing
- **Kernel TCP buffers** — 16 MB
- **`ulimit -n = 65536`** — for many concurrent WebSocket connections
- **`vm.swappiness = 10`** — avoid paging the trading bot
- **`chrony`** — time sync with `ntp-a2.nict.jp` (Japan stratum-1), offset ~µs
- **`uvloop`** — 2-4× faster asyncio
- **Python bytecode** — pre-compiled at deploy time
- **systemd `Nice=-5`** — higher CPU priority for the bot

---

## Filesystem Layout

```
/opt/ratebridge/              # Git working copy
  .env                        # secrets (chmod 600)
  .venv/                      # Python virtualenv
  config.yaml                 # trading/risk parameters
  docker-compose.yml          # Redis container
  main.py                     # bot entry
  src/                        # bot source
  api/                        # FastAPI routes
  frontend/
    build/                    # React static (served by nginx)
  ops/
    status.sh                 # system overview
    logs.sh                   # pretty log tail
    deploy.sh                 # pull + rebuild + restart
    healthcheck.sh            # cron alert script

/etc/systemd/system/
  ratebridge.service          # bot service unit

/etc/nginx/sites-available/
  ratebridge                  # nginx site config

/etc/cron.d/
  ratebridge-healthcheck      # 5-min Telegram alerts

/var/log/ratebridge/          # additional logs (journald is primary)
/var/lib/ratebridge/          # runtime state (healthcheck)
```

---

## Operational Commands

Aliases installed in `/etc/profile.d/ratebridge.sh` — take effect on next SSH login.

| Alias | Action |
|---|---|
| `rb-status` | Full system overview (bot, redis, nginx, resources) |
| `rb-logs` | Tail bot logs with color coding |
| `rb-deploy` | Pull, rebuild frontend, restart bot |
| `rb-start` / `rb-stop` / `rb-restart` | systemd control |
| `rb-edit-env` | Edit `.env` and auto-restart |

---

## Initial Deploy (reference)

If re-deploying from scratch:

```bash
# 1. Install Python 3.11 + Node 20 + Docker + chrony + nginx
apt install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt install -y python3.11 python3.11-venv python3.11-dev chrony nginx
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
curl -fsSL https://get.docker.com | sh

# 2. Clone repo
git clone https://github.com/Shmuel18/Arbitrage-.git /opt/ratebridge
cd /opt/ratebridge
git checkout v3-live

# 3. Python venv
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Frontend build
cd frontend && npm install && npm run build && cd ..

# 5. Copy .env and fill API keys
cp .env.example .env
chmod 600 .env
nano .env

# 6. Start Redis
docker compose up -d

# 7. Install systemd + nginx from this repo's reference config
#    (see DEPLOYMENT.md appendix)
systemctl enable --now ratebridge

# 8. Open ports
ufw allow 22,80,443/tcp
```

---

## Going Live (from paper → live trading)

1. **Verify whitelist** — each exchange has server IP `149.28.23.129`
2. **Verify config** — `config.yaml` limits look right
3. **Edit `.env`** — flip `PAPER_TRADING=false` and `DRY_RUN=false`
4. **Restart** — `rb-restart`
5. **Watch logs** — `rb-logs`, watch for first trade notifications
6. **Confirm in Telegram** — open/close events arrive

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot won't start | `journalctl -u ratebridge -n 100 --no-pager` |
| "Redis connection refused" | `docker ps \| grep trinity-redis` |
| "Invalid API-key, IP" | Exchange IP whitelist — must include `149.28.23.129` |
| `/api/` returns 502 | Bot is down; check systemctl status |
| High latency to exchanges | `chronyc tracking` — if offset > 50 ms, restart chrony |
| Disk getting full | `ncdu /` — check `/var/log/journal` size |

---

## Cost Breakdown

| Item | Monthly |
|---|---|
| Vultr VPS (High-Frequency Intel, 2c/4GB/128GB NVMe) | $24.00 |
| Vultr Automatic Backups (20%) | $4.80 |
| **Total** | **$28.80** |

Future (when adding domain + SSL):
- Domain registration (.xyz first year) | $2/year
- Let's Encrypt SSL certificate | Free

---

## Contact / Ops

- **Healthcheck alerts** → Telegram bot (configured in `.env`)
- **Server access** — SSH key only, `ssh root@149.28.23.129`
- **Repo** — https://github.com/Shmuel18/Arbitrage-
