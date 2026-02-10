# 🚀 Trinity Setup Guide

Complete step-by-step guide to get Trinity Arbitrage Engine running.

---

## 📋 Prerequisites

### System Requirements

- Python 3.11 or higher
- PostgreSQL 14+ (TimescaleDB recommended)
- Redis 7+
- 4GB+ RAM
- Stable internet connection
- Low-latency VPS (Tokyo/Dublin) for production

### Knowledge Requirements

- Basic understanding of cryptocurrency trading
- Familiarity with command line
- Understanding of futures/perpetual contracts
- Risk management principles

---

## 🔧 Installation

### Step 1: Clone & Navigate

```powershell
cd "c:\Users\shh92\Documents\Arbitrage"
```

### Step 2: Create Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\activate
```

You should see `(venv)` in your prompt.

### Step 3: Install Dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

This will take a few minutes. Wait for completion.

---

## 🗄️ Database Setup

### Option A: Local PostgreSQL (Development)

1. **Install PostgreSQL**
   - Download from: https://www.postgresql.org/download/windows/
   - Install with default settings
   - Remember the password you set

2. **Create Database**

```powershell
# Connect to PostgreSQL
psql -U postgres

# In psql prompt:
CREATE DATABASE trinity_arbitrage;
CREATE USER trinity WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE trinity_arbitrage TO trinity;
\q
```

3. **Optional: Install TimescaleDB**
   - Download from: https://www.timescale.com/
   - Better performance for time-series data

### Option B: Docker (Recommended)

```powershell
# Create docker-compose.yml
docker-compose up -d postgres redis
```

---

## 🔴 Redis Setup

### Option A: Local Redis

1. **Download Redis for Windows**
   - From: https://github.com/microsoftarchive/redis/releases
   - Or use WSL2

2. **Start Redis**

```powershell
redis-server
```

### Option B: Docker

```powershell
docker run -d -p 6379:6379 --name trinity-redis redis:7-alpine
```

---

## ⚙️ Configuration

### Step 1: Create Environment File

```powershell
cp .env.example .env
```

### Step 2: Edit .env File

Open `.env` in a text editor and fill in:

```env
# Database (from Step above)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=trinity_arbitrage
POSTGRES_USER=trinity
POSTGRES_PASSWORD=your_secure_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Exchange API Keys - Binance
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
BINANCE_TESTNET=true  # Use testnet for development

# Exchange API Keys - Bybit
BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_api_secret
BYBIT_TESTNET=true

# Exchange API Keys - OKX
OKX_API_KEY=your_okx_api_key
OKX_API_SECRET=your_okx_api_secret
OKX_PASSPHRASE=your_okx_passphrase
OKX_TESTNET=true

# Safety
PAPER_TRADING=true
DRY_RUN=true

# Monitoring (optional)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

### Step 3: Review config.yaml

Open `config.yaml` and adjust:

- `risk_limits`: Keep conservative for testing
- `trading_params`: Adjust minimum profit thresholds
- `symbols.watchlist`: Choose trading pairs

---

## 🔑 Getting API Keys

### Binance

1. Go to: https://testnet.binancefuture.com (testnet)
2. Create account
3. API Management → Create API Key
4. Enable "Futures" permissions
5. Whitelist your IP (recommended)

### Bybit

1. Go to: https://testnet.bybit.com (testnet)
2. Create account
3. API → Create New Key
4. Enable "Contract" permissions
5. Save key and secret

### OKX

1. Go to: https://www.okx.com/account/my-api (requires main account)
2. Create API Key
3. Enable "Trade" permissions
4. Note the passphrase
5. Enable paper trading mode

---

## 🏗️ Initialize Database

```powershell
python scripts/setup_db.py
```

You should see:

```
✅ Database setup completed successfully
```

---

## ✅ Health Check

Verify everything is working:

```powershell
python scripts/health_check.py
```

Expected output:

```
✅ All health checks passed - System ready
```

If you see errors, review the output and fix the issues.

---

## 🎮 Running Trinity

### Paper Trading Mode (Safe)

```powershell
python main.py --paper
```

This mode:

- ✅ Discovers opportunities
- ✅ Logs decisions
- ❌ Does NOT place real orders
- Perfect for testing

### Dry Run Mode (Testing)

Edit `.env`:

```env
PAPER_TRADING=false
DRY_RUN=true
```

Then:

```powershell
python main.py
```

This mode:

- ✅ Connects to exchanges
- ✅ Validates orders
- ❌ Does NOT execute orders
- Good for validation

### Live Mode (Production) ⚠️

**ONLY AFTER THOROUGH TESTING**

Edit `.env`:

```env
PAPER_TRADING=false
DRY_RUN=false
BINANCE_TESTNET=false
BYBIT_TESTNET=false
```

Then:

```powershell
python main.py --live
```

You will be prompted for confirmation.

⚠️ **WARNING**: Real capital at risk!

---

## 📊 Monitoring

### Logs

Check logs in `logs/` directory:

```powershell
# View latest log
Get-Content logs\trinity_YYYYMMDD.log -Tail 50 -Wait
```

### Prometheus Metrics

Access at: http://localhost:9090

### Telegram Alerts

Configure bot token and chat ID in `.env` for instant alerts.

---

## 🧪 Testing

Run tests:

```powershell
pytest tests/
```

With coverage:

```powershell
pytest --cov=src --cov-report=html
```

---

## 🔍 Troubleshooting

### "Connection refused" errors

- Check PostgreSQL is running: `pg_isready`
- Check Redis is running: `redis-cli ping`
- Verify ports in `.env` match running services

### "API authentication failed"

- Verify API keys are correct
- Check API key permissions
- Ensure IP is whitelisted
- Verify testnet vs mainnet

### "Module not found" errors

- Ensure virtual environment is activated
- Reinstall: `pip install -r requirements.txt`

### Performance issues

- Increase database pool size in `config.yaml`
- Check network latency to exchanges
- Consider VPS closer to exchange servers

---

## 🎓 Next Steps

1. **Learn the System**
   - Read technical design document
   - Study code structure
   - Review state machine flows

2. **Paper Trading**
   - Run for 1+ weeks
   - Monitor logs
   - Analyze decisions
   - Verify calculations

3. **Testnet Trading**
   - Use exchange testnets
   - Test with realistic sizes
   - Validate all scenarios
   - Test error handling

4. **Small Capital**
   - Start with < $1,000
   - Monitor closely
   - Increase gradually
   - Build confidence

5. **Scale Up**
   - Increase position sizes
   - Add more symbols
   - Optimize parameters
   - Monitor performance

---

## 📚 Additional Resources

### Documentation

- See README.md for overview
- Review technical design doc
- Check inline code comments

### Support

- Check logs first
- Review health check output
- Test with paper trading
- Verify configuration

---

## ⚖️ Legal & Risk Disclaimer

**READ CAREFULLY**

This software is provided "as is" without warranty of any kind.

- Cryptocurrency trading carries substantial risk
- You can lose all your capital
- Past performance ≠ future results
- No guarantee of profits
- Bugs may exist despite testing
- Exchange APIs can fail
- Network issues can cause losses

**YOU ARE RESPONSIBLE FOR:**

- Understanding the code
- Testing thoroughly
- Managing risk
- Your own trading decisions
- All losses incurred

**NEVER:**

- Trade with money you can't afford to lose
- Run without understanding the code
- Skip testing phases
- Ignore risk limits
- Trade while impaired

---

## ✅ Pre-Flight Checklist

Before live trading, verify:

- [ ] All health checks pass
- [ ] Paper trading runs successfully
- [ ] Testnet trading completed
- [ ] Risk limits configured
- [ ] Monitoring alerts working
- [ ] Database backups configured
- [ ] API keys secured
- [ ] Emergency stop plan ready
- [ ] Capital allocated appropriately
- [ ] Understanding complete

---

**Good luck, trade safely! 🚀**
