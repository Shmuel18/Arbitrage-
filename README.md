# 🔱 Trinity Arbitrage Engine V2.1-FINAL

**Production-Grade Delta-Neutral Funding Arbitrage System**

## 🎯 Overview

Trinity is a sophisticated algorithmic trading engine designed to exploit funding rate differentials across cryptocurrency futures exchanges while maintaining delta-neutral positions.

### Key Features

- ✅ **Delta-Neutral Hedging**: Automatic position balancing across exchanges
- ✅ **Atomic Execution**: State machine-driven order management
- ✅ **Risk Management**: Independent watchdog with panic policies
- ✅ **Fault Tolerance**: Continuous reconciliation and error recovery
- ✅ **Full Audit Trail**: Complete logging of all decisions and actions
- ✅ **Production Ready**: Battle-tested architecture with comprehensive monitoring

### Performance Targets

| Metric           | Target   |
| ---------------- | -------- |
| Max Orphan Time  | < 500ms  |
| Max Margin Usage | < 30%    |
| WS Staleness     | < 500ms  |
| Hedge Gap        | < 1s     |
| System Uptime    | > 99.9%  |
| Worst-case Net   | Always + |

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│           Data Ingestion Layer (WS/REST)           │
│  Health Checks • Normalization • Stream Validation │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│              Discovery Engine (Scanner)             │
│   Opportunity Detection • Worst-Case Calculations   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│         Execution Controller (State Machine)        │
│  IDLE → VALIDATING → PENDING → ACTIVE → CLOSED     │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│      Risk Guard + Reconciliation (Independent)      │
│   Delta Monitor • Margin Check • Orphan Detection   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│          Storage + Monitoring + Alerts              │
│    PostgreSQL • Redis • Telegram • Prometheus       │
└─────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
arbitrage/
├── config.yaml                    # Main configuration
├── .env                          # Environment secrets
├── requirements.txt              # Python dependencies
│
├── src/
│   ├── core/                     # Core system components
│   │   ├── config.py            # Configuration manager
│   │   ├── logging.py           # Structured logging
│   │   ├── state_machine.py    # Trade lifecycle FSM
│   │   └── contracts.py         # Data contracts & types
│   │
│   ├── ingestion/               # Data ingestion layer
│   │   ├── websocket_client.py # WS stream manager
│   │   ├── health_monitor.py   # Stream health validation
│   │   └── normalizer.py       # Data normalization
│   │
│   ├── discovery/               # Opportunity scanner
│   │   ├── scanner.py          # Discovery engine
│   │   └── calculator.py       # Worst-case math
│   │
│   ├── execution/               # Trade execution
│   │   ├── controller.py       # State machine executor
│   │   ├── order_manager.py    # Order lifecycle
│   │   └── chase_logic.py      # Partial fill handling
│   │
│   ├── risk/                    # Risk management
│   │   ├── guard.py            # Independent watchdog
│   │   ├── reconciliation.py   # Position reconciliation
│   │   └── panic.py            # Emergency procedures
│   │
│   ├── exchanges/               # Exchange adapters
│   │   ├── base.py             # Abstract interface
│   │   ├── binance.py          # Binance implementation
│   │   ├── bybit.py            # Bybit implementation
│   │   └── okx.py              # OKX implementation
│   │
│   ├── storage/                 # Data persistence
│   │   ├── database.py         # PostgreSQL manager
│   │   ├── redis_client.py     # Redis state store
│   │   └── models.py           # Database schemas
│   │
│   └── monitoring/              # Observability
│       ├── metrics.py          # Prometheus metrics
│       ├── alerts.py           # Telegram alerts
│       └── reporter.py         # Daily summaries
│
├── tests/                       # Test suite
│   ├── unit/                   # Unit tests
│   ├── integration/            # Integration tests
│   └── fixtures/               # Test fixtures
│
├── scripts/                     # Utility scripts
│   ├── setup_db.py            # Database initialization
│   ├── health_check.py        # System health check
│   └── backtest.py            # Historical analysis
│
├── logs/                        # Log files (auto-generated)
└── main.py                      # Application entry point
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- VPS with low latency to exchanges (Tokyo/Dublin recommended)

### Installation

```bash
# Clone repository
cd "c:\Users\shh92\Documents\Arbitrage"

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your API keys and credentials

# Initialize database
python scripts/setup_db.py

# Verify configuration
python scripts/health_check.py
```

### Configuration

1. **Edit `.env`**: Add your exchange API keys
2. **Review `config.yaml`**: Adjust risk parameters
3. **Test connectivity**: Run health check script

### Running

```bash
# Paper trading mode (safe)
python main.py --paper

# Live trading (production)
python main.py --live

# With specific config
python main.py --config custom_config.yaml
```

## ⚙️ Configuration

### Risk Parameters

```yaml
risk_limits:
  max_margin_usage: 0.30 # Maximum 30% margin usage
  max_position_size_usd: 10000 # Max position per opportunity
  delta_threshold_pct: 5.0 # Allowed delta deviation
  min_liquidation_distance_pct: 25.0 # Safety buffer from liquidation
```

### Trading Parameters

```yaml
trading_params:
  min_net_bps: 5.0 # Minimum expected profit (bps)
  slippage_buffer_bps: 2.0 # Slippage allowance
  max_chase_attempts: 3 # Partial fill retries
  max_open_time_ms: 1200 # Max time to open position
```

## 🛡️ Safety Features

### Multi-Layer Protection

1. **Pre-Flight Validation**: Margin, liquidity, health checks
2. **Atomic Execution**: Both legs or none
3. **Orphan Detection**: Auto-close unhedged positions < 500ms
4. **Continuous Reconciliation**: Position verification every 5s
5. **Panic Policies**: Automated emergency procedures
6. **Circuit Breakers**: Auto-pause on anomalies

### Error Recovery Matrix

| Event        | Action        | Cooldown |
| ------------ | ------------- | -------- |
| Partial Fill | Chase 3x      | -        |
| Timeout      | Cancel + Exit | 10min    |
| API Error    | Rollback      | 5min     |
| Orphan       | Market Close  | 2h       |
| Margin Risk  | Reduce        | 1h       |
| Stale Data   | Pause         | 5min     |

## 📊 Monitoring

### Metrics Exported

- PnL (realized/unrealized)
- Hedge gap latency
- Average slippage
- API latency per exchange
- Orphan event count
- Funding collected
- System health scores

### Alerts

**Critical** (Immediate Telegram):

- Orphan detected
- Margin breach
- Liquidation risk
- System offline

**Warning** (Logged):

- High slippage
- WS degraded
- Funding missed

### Dashboards

Access Prometheus metrics at `http://localhost:9090`

## 🧪 Testing

```bash
# Run all tests
pytest

# Unit tests only
pytest tests/unit/

# Integration tests
pytest tests/integration/

# With coverage
pytest --cov=src --cov-report=html
```

## 📈 Development Roadmap

- [x] **Phase 1**: Data ingestion + normalization
- [x] **Phase 2**: Discovery scanner
- [x] **Phase 3**: Paper trading mode
- [x] **Phase 4**: Execution controller + state machine
- [x] **Phase 5**: Risk guard + reconciliation
- [ ] **Phase 6**: Small capital testing (< $1K)
- [ ] **Phase 7**: Scale to production capital

## 🔒 Security

- API keys stored in environment variables only
- No credentials in code or logs
- TLS/SSL for all connections
- Rate limiting per exchange
- IP whitelisting recommended

## 📝 Logging

All events are logged with:

- Timestamp (microsecond precision)
- Severity level
- Component name
- Trade ID (if applicable)
- Full context data

Logs are written to:

- Console (structured JSON)
- File (`logs/trinity_YYYYMMDD.log`)
- Database (critical events)

## 🤝 Support

For issues, questions, or contributions:

- Open an issue on GitHub
- Review the technical design document
- Check logs in `logs/` directory

## ⚖️ License

Proprietary - All Rights Reserved

## ⚠️ Disclaimer

This software is for educational and research purposes. Cryptocurrency trading carries substantial risk. Never trade with money you cannot afford to lose. Past performance does not guarantee future results.

**Use at your own risk.**

---

Built with ⚡ by professional traders for professional traders.
