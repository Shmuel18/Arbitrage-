# 🚀 Trinity Quick Reference Guide

## 🏃 Quick Commands

### Setup

```powershell
# First time setup
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings
python scripts/setup_db.py
python scripts/health_check.py
```

### Running

```powershell
# Paper trading (safest)
python main.py --paper

# Dry run (validates but doesn't execute)
python main.py

# Live trading (DANGEROUS)
python main.py --live
```

### Monitoring

```powershell
# View logs (real-time)
Get-Content logs\trinity_*.log -Tail 50 -Wait

# Check system health
python scripts/health_check.py

# Docker services status
docker-compose ps
```

---

## 📁 Important Files

| File                                               | Purpose              |
| -------------------------------------------------- | -------------------- |
| [config.yaml](config.yaml)                         | Main configuration   |
| [.env](.env)                                       | API keys & secrets   |
| [main.py](main.py)                                 | Application entry    |
| [README.md](README.md)                             | Project overview     |
| [SETUP_GUIDE.md](SETUP_GUIDE.md)                   | Installation guide   |
| [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) | Production checklist |
| [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)           | What's built         |

---

## 🔧 Configuration Quick Reference

### Risk Limits (config.yaml)

```yaml
risk_limits:
  max_margin_usage: 0.30 # Max 30% of capital
  max_position_size_usd: 10000 # Max $10K per position
  max_orphan_time_ms: 500 # Max unhedged time
  delta_threshold_pct: 5.0 # Max delta deviation
```

### Trading Parameters

```yaml
trading_params:
  min_net_bps: 5.0 # Min profit after costs
  max_chase_attempts: 3 # Partial fill retries
  max_open_time_ms: 1200 # 1.2s to open position
```

### Environment (.env)

```env
PAPER_TRADING=true              # Safe mode
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
POSTGRES_PASSWORD=your_db_pass
TELEGRAM_BOT_TOKEN=your_token
```

---

## 🎯 Common Tasks

### Check System Health

```powershell
python scripts/health_check.py
```

✅ All checks should pass before trading

### View Recent Logs

```powershell
Get-Content logs\trinity_YYYYMMDD.log -Tail 100
```

### Query Database

```sql
-- Recent trades
SELECT * FROM trades ORDER BY timestamp_created DESC LIMIT 10;

-- Open positions
SELECT * FROM trades WHERE status = 'active_hedged';

-- Today's P&L
SELECT
    COUNT(*) as trades,
    SUM(realized_pnl_usd) as total_pnl,
    SUM(total_fees_usd) as total_fees
FROM trades
WHERE DATE(timestamp_closed) = CURRENT_DATE;
```

### Emergency Stop

```powershell
# CTRL+C in terminal or:
taskkill /F /IM python.exe
```

---

## 📊 Key Metrics to Monitor

### System Health

- ✅ WebSocket staleness < 500ms
- ✅ Exchange status = HEALTHY
- ✅ Redis/DB connection = UP
- ✅ No errors in logs

### Trading Performance

- Win rate > 70%
- Orphan events = 0
- Margin usage < 30%
- Avg latency < 300ms
- Hedge gap < 1s

### Risk Metrics

- Delta breach events = 0
- Liquidation distance > 25%
- Max drawdown acceptable
- Slippage within expectations

---

## 🚨 Troubleshooting

### "Connection refused"

```powershell
# Check services
docker-compose ps

# Restart if needed
docker-compose restart postgres redis
```

### "API authentication failed"

- Verify API keys in .env
- Check key permissions
- Confirm testnet vs mainnet
- Try regenerating keys

### "Module not found"

```powershell
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### High latency

- Check network connection
- Verify VPS location
- Review rate limits
- Check exchange status

---

## 📈 Performance Optimization

### Phase 1: Conservative (First Month)

```yaml
max_position_size_usd: 1000
min_net_bps: 10.0
concurrent_opportunities: 1
```

### Phase 2: Moderate (After Proving)

```yaml
max_position_size_usd: 5000
min_net_bps: 5.0
concurrent_opportunities: 3
```

### Phase 3: Aggressive (Proven System)

```yaml
max_position_size_usd: 10000
min_net_bps: 3.0
concurrent_opportunities: 5
```

---

## 🔐 Security Checklist

- [ ] API keys in .env only (not in code)
- [ ] .env in .gitignore
- [ ] Strong database password
- [ ] IP whitelisting enabled
- [ ] 2FA on exchanges
- [ ] Testnet for development
- [ ] Paper trading first
- [ ] Regular backups

---

## 📞 Emergency Contacts

| Issue           | Action                           |
| --------------- | -------------------------------- |
| System crash    | Check logs, restart service      |
| Orphan position | Manual close on exchange         |
| Margin call     | Deposit funds or close positions |
| API down        | Wait for exchange recovery       |
| Database full   | Clear old logs, expand storage   |

---

## 💡 Pro Tips

1. **Start Small**: Begin with $500-1000
2. **Monitor Daily**: Review all trades
3. **Log Everything**: Audit trail is critical
4. **Test Thoroughly**: Paper → Testnet → Live
5. **Stay Conservative**: Better safe than sorry
6. **Have Stop Loss**: Know when to quit
7. **Regular Backups**: Protect your data
8. **Document Changes**: Track what you modify

---

## 📚 Learning Path

### Week 1: Understanding

- Read all documentation
- Study the code structure
- Understand the state machine
- Review risk calculations

### Week 2: Paper Trading

- Run system continuously
- Monitor discovery logs
- Verify calculations
- Test error scenarios

### Week 3: Testnet

- Use exchange testnets
- Place real orders (no real money)
- Test full workflow
- Validate reconciliation

### Week 4: Small Capital

- Start with < $1K
- Monitor obsessively
- Build confidence
- Iterate on parameters

### Month 2+: Scale

- Increase capital gradually
- Optimize parameters
- Add more symbols
- Improve performance

---

## 🎓 Key Concepts

### Funding Rate Arbitrage

- Long on low funding exchange
- Short on high funding exchange
- Collect funding differential
- Close when rate converges

### Delta Neutral

- Net position ≈ 0
- No directional exposure
- Price movement doesn't matter
- Only funding matters

### Worst Case Math

- Assume taker fees always
- Cross spread every time
- Add safety buffers
- Still profitable = good trade

### State Machine

- IDLE → VALIDATING → PRE_FLIGHT → PENDING_OPEN
- → OPEN_PARTIAL (optional) → ACTIVE_HEDGED
- → PENDING_CLOSE → RECONCILIATION → CLOSED

---

## ⚖️ Risk Management Rules

1. **Never exceed max margin** (30%)
2. **Always close orphans** (<500ms)
3. **Monitor liquidation distance** (>25%)
4. **Enforce cooldowns** (after incidents)
5. **Validate reconciliation** (every 5s)
6. **Respect stop loss** (system or manual)

---

## 🔄 Maintenance Schedule

### Daily

- [ ] Review logs
- [ ] Check P&L
- [ ] Monitor alerts
- [ ] Verify health

### Weekly

- [ ] Analyze performance
- [ ] Review incidents
- [ ] Optimize parameters
- [ ] Backup database

### Monthly

- [ ] Comprehensive analysis
- [ ] Update documentation
- [ ] Review risk limits
- [ ] Plan improvements

---

## 📞 Support Resources

- **Documentation**: See README.md and guides
- **Health Check**: `python scripts/health_check.py`
- **Logs**: Check `logs/` directory
- **Database**: Query for detailed analysis
- **Monitoring**: Check Telegram alerts

---

**Last Updated**: February 2026
**Version**: 2.1.0-FINAL

---

🚀 **Keep this guide handy while operating Trinity!**
