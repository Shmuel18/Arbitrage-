"""
Trade Journal — persistent JSON-Lines log capturing every significant event.

Each line is a self-contained JSON object with:
  ts, event, trade_id (optional), data (event-specific payload)

Events:
  trade_open     — new trade opened
  trade_close    — trade closed with PnL summary
  funding_paid   — funding payment detected for an active trade
  hold_decision  — decided to hold after funding
  exit_decision  — decided to exit after funding
  balance_snapshot — periodic exchange balance record
  basis_reject   — opportunity passed gates but rejected by basis inversion
  error          — any error event

The journal file lives at logs/trade_journal.jsonl.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from logging.handlers import RotatingFileHandler


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


class TradeJournal:
    """Append-only structured event logger for trade audit trail."""

    def __init__(self, log_dir: str = "logs", max_mb: int = 200, backup_count: int = 5):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._log_dir / "trade_journal.jsonl"

        # Use a dedicated logger so it doesn't interfere with main logs
        self._logger = logging.getLogger("trade_journal")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        if not self._logger.handlers:
            fh = RotatingFileHandler(
                str(self._path),
                maxBytes=max_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )
            # Raw line formatter — we write our own JSON
            fh.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(fh)

    def _write(self, event: str, trade_id: str = None, **data):
        doc = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        if trade_id:
            doc["trade_id"] = trade_id
        if data:
            doc["data"] = data
        self._logger.info(json.dumps(doc, cls=_DecimalEncoder, ensure_ascii=False))

    # ── Event methods ────────────────────────────────────────────

    def trade_opened(self, trade_id: str, symbol: str, mode: str,
                     long_exchange: str, short_exchange: str,
                     long_qty, short_qty,
                     entry_price_long, entry_price_short,
                     long_funding_rate, short_funding_rate,
                     spread_pct, net_pct,
                     exit_before=None, n_collections=0,
                     notional=None, entry_reason=None, **extra):
        self._write(
            "trade_open", trade_id,
            symbol=symbol, mode=mode,
            long_exchange=long_exchange, short_exchange=short_exchange,
            long_qty=long_qty, short_qty=short_qty,
            entry_price_long=entry_price_long,
            entry_price_short=entry_price_short,
            long_funding_rate=long_funding_rate,
            short_funding_rate=short_funding_rate,
            spread_pct=spread_pct, net_pct=net_pct,
            exit_before=exit_before,
            n_collections=n_collections,
            notional=notional,
            entry_reason=entry_reason,
            **extra,
        )

    def trade_closed(self, trade_id: str, symbol: str, mode: str,
                     duration_min: float,
                     entry_price_long=None, entry_price_short=None,
                     exit_price_long=None, exit_price_short=None,
                     long_pnl=None, short_pnl=None,
                     price_pnl=None, funding_income=None,
                     funding_cost=None, funding_net=None,
                     fees=None, net_profit=None,
                     profit_pct=None, invested=None,
                     exit_reason: str = "",
                     entry_funding_long=None, entry_funding_short=None,
                     exit_funding_long=None, exit_funding_short=None,
                     **extra):
        self._write(
            "trade_close", trade_id,
            symbol=symbol, mode=mode,
            duration_min=duration_min,
            entry_price_long=entry_price_long,
            entry_price_short=entry_price_short,
            exit_price_long=exit_price_long,
            exit_price_short=exit_price_short,
            long_pnl=long_pnl,
            short_pnl=short_pnl,
            price_pnl=price_pnl,
            funding_income=funding_income,
            funding_cost=funding_cost,
            funding_net=funding_net,
            fees=fees,
            net_profit=net_profit,
            profit_pct=profit_pct,
            invested=invested,
            exit_reason=exit_reason,
            entry_funding_long=entry_funding_long,
            entry_funding_short=entry_funding_short,
            exit_funding_long=exit_funding_long,
            exit_funding_short=exit_funding_short,
            **extra,
        )

    def funding_detected(self, trade_id: str, symbol: str,
                         exchange: str, side: str,
                         rate=None, estimated_payment=None):
        self._write(
            "funding_paid", trade_id,
            symbol=symbol, exchange=exchange, side=side,
            rate=rate, estimated_payment=estimated_payment,
        )

    def funding_collected(self, trade_id: str, symbol: str,
                          collection_num: int,
                          long_exchange: str, short_exchange: str,
                          long_rate=None, short_rate=None,
                          long_payment_usd=None, short_payment_usd=None,
                          net_payment_usd=None,
                          cumulative_usd=None,
                          immediate_spread=None):
        """Log one complete funding cycle with per-payment USD amounts."""
        self._write(
            "funding_collected", trade_id,
            symbol=symbol,
            collection_num=collection_num,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            long_rate=long_rate,
            short_rate=short_rate,
            long_payment_usd=long_payment_usd,
            short_payment_usd=short_payment_usd,
            net_payment_usd=net_payment_usd,
            cumulative_usd=cumulative_usd,
            immediate_spread=immediate_spread,
        )

    def position_snapshot(self, trade_id: str, symbol: str,
                          minutes_since_funding: int,
                          long_exchange: str, short_exchange: str,
                          long_price=None, short_price=None,
                          immediate_spread=None,
                          long_pnl_usd=None, short_pnl_usd=None,
                          price_pnl_usd=None,
                          funding_collected_usd=None):
        """5-minute snapshot after funding payment — price + spread at that moment."""
        self._write(
            "position_snapshot", trade_id,
            symbol=symbol,
            minutes_since_funding=minutes_since_funding,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            long_price=long_price,
            short_price=short_price,
            immediate_spread=immediate_spread,
            long_pnl_usd=long_pnl_usd,
            short_pnl_usd=short_pnl_usd,
            price_pnl_usd=price_pnl_usd,
            funding_collected_usd=funding_collected_usd,
        )

    def hold_decision(self, trade_id: str, symbol: str,
                      immediate_spread=None, next_funding_min=None):
        self._write(
            "hold_decision", trade_id,
            symbol=symbol,
            immediate_spread=immediate_spread,
            next_funding_min=next_funding_min,
        )

    def exit_decision(self, trade_id: str, symbol: str,
                      reason: str, immediate_spread=None,
                      hold_min: int = 0):
        self._write(
            "exit_decision", trade_id,
            symbol=symbol, reason=reason,
            immediate_spread=immediate_spread,
            hold_min=hold_min,
        )

    def balance_snapshot(self, balances: dict, total: float = None):
        self._write(
            "balance_snapshot",
            balances=balances,
            total=total,
        )

    def basis_rejection(self, symbol: str,
                        long_exchange: str, short_exchange: str,
                        basis_loss=None, net_edge=None,
                        long_ask=None, short_bid=None):
        self._write(
            "basis_reject",
            symbol=symbol,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            basis_loss=basis_loss,
            net_edge=net_edge,
            long_ask=long_ask,
            short_bid=short_bid,
        )

    def error(self, msg: str, trade_id: str = None, **extra):
        self._write("error", trade_id, message=msg, **extra)

    def event(self, event_name: str, trade_id: str = None, **data):
        """Generic event for anything not covered above."""
        self._write(event_name, trade_id, **data)


# ── Singleton ────────────────────────────────────────────────────
_instance: TradeJournal = None

def get_journal(log_dir: str = "logs") -> TradeJournal:
    global _instance
    if _instance is None:
        _instance = TradeJournal(log_dir=log_dir)
    return _instance
