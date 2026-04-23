"""Backtesting framework for the RateBridge funding-rate arbitrage strategy.

Phased build:
    Phase 1 — data ingestion (scripts/fetch_historical_data.py, storage helpers)
    Phase 2 — event-driven engine + portfolio state
    Phase 3 — reporting (equity curve, Sharpe, max DD, per-trade breakdown)

See HANDOFF.md section "P2" and the design discussion in the chat transcript
for the architecture rationale.
"""
