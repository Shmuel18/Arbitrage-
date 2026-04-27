"""Lightweight dry-run check that mirrors the Sizer's pre-flight gates.

Used by the scanner before publishing opportunities so the dashboard can
flag rows that are SCANNER-qualified but won't actually be entered (most
commonly: notional-too-small-for-min-lot when one leg's exchange is low
on margin). Without this, a row appears "above threshold" with a green
TOP badge yet the bot silently never enters it.

Pure heuristics — no order placement, no balance fetches over the wire.
Reads only adapter-cached instrument specs and a balances snapshot
already published to Redis.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, FrozenSet, Iterable, Optional

if TYPE_CHECKING:
    from src.core.config import Config
    from src.core.contracts import OpportunityCandidate
    from src.exchanges.adapter import ExchangeAdapter, ExchangeManager

# Status enum — kept as plain strings so they survive JSON round-trips
# unchanged and the frontend can map directly. Renaming requires a
# coordinated frontend edit (utils/format + i18n).
STATUS_READY = "ready"
STATUS_ALREADY_OPEN = "already_open"
STATUS_INSUFFICIENT_BALANCE = "insufficient_balance"
STATUS_LOT_SIZE_TOO_LARGE = "lot_size_too_large"
STATUS_UNKNOWN = "unknown"

_DEFAULT_LEVERAGE = 5
_MIN_BAL_USD = 5.0
_FALLBACK_LOT = 0.001


async def compute_executable_status(
    opp: "OpportunityCandidate",
    balances: Dict[str, float],
    long_adapter: "ExchangeAdapter",
    short_adapter: "ExchangeAdapter",
    cfg: "Config",
    busy_symbols: FrozenSet[str] = frozenset(),
) -> str:
    """Return one of the STATUS_* strings for a single opportunity.

    Returns STATUS_UNKNOWN on any internal error so a missing instrument
    spec doesn't block UI rendering.
    """
    try:
        if opp.symbol in busy_symbols:
            return STATUS_ALREADY_OPEN

        long_bal = float(balances.get(opp.long_exchange, 0) or 0)
        short_bal = float(balances.get(opp.short_exchange, 0) or 0)
        if long_bal < _MIN_BAL_USD or short_bal < _MIN_BAL_USD:
            return STATUS_INSUFFICIENT_BALANCE

        long_exc_cfg = cfg.exchanges.get(opp.long_exchange)
        short_exc_cfg = cfg.exchanges.get(opp.short_exchange)
        long_lev = int(long_exc_cfg.leverage) if (long_exc_cfg and long_exc_cfg.leverage) else _DEFAULT_LEVERAGE
        short_lev = int(short_exc_cfg.leverage) if (short_exc_cfg and short_exc_cfg.leverage) else _DEFAULT_LEVERAGE
        lev = min(long_lev, short_lev)
        pos_pct = float(cfg.risk_limits.position_size_pct)
        notional = min(long_bal, short_bal) * pos_pct * lev

        long_spec, short_spec = await asyncio.gather(
            long_adapter.get_instrument_spec(opp.symbol),
            short_adapter.get_instrument_spec(opp.symbol),
            return_exceptions=True,
        )
        if isinstance(long_spec, Exception) or isinstance(short_spec, Exception):
            return STATUS_UNKNOWN
        if not long_spec or not short_spec:
            return STATUS_UNKNOWN

        long_lot = float(long_spec.lot_size or _FALLBACK_LOT) * float(long_spec.contract_size or 1)
        short_lot = float(short_spec.lot_size or _FALLBACK_LOT) * float(short_spec.contract_size or 1)
        lot = max(long_lot, short_lot)
        price = float(opp.reference_price or 0)
        if price <= 0:
            return STATUS_UNKNOWN

        if (notional / price) < lot:
            return STATUS_LOT_SIZE_TOO_LARGE

        return STATUS_READY
    except Exception:
        # Fail-open — better to render the badge as "unknown" than to break
        # the whole publish path on a single missing field.
        return STATUS_UNKNOWN


async def compute_statuses_for(
    opps: Iterable["OpportunityCandidate"],
    balances: Dict[str, float],
    exchange_mgr: "ExchangeManager",
    cfg: "Config",
    busy_symbols: FrozenSet[str] = frozenset(),
) -> list[Optional[str]]:
    """Compute statuses for a batch of opportunities in parallel.

    Returns a list aligned with the input order. Entries are None when the
    opportunity is not qualified (so the UI keeps the existing "below
    threshold" rendering instead of showing a misleading READY badge).
    """
    async def _one(o: "OpportunityCandidate") -> Optional[str]:
        if not getattr(o, "qualified", True):
            return None
        long_ad = exchange_mgr.get(o.long_exchange)
        short_ad = exchange_mgr.get(o.short_exchange)
        if not long_ad or not short_ad:
            return STATUS_UNKNOWN
        return await compute_executable_status(
            o, balances, long_ad, short_ad, cfg, busy_symbols,
        )

    results = await asyncio.gather(
        *(_one(o) for o in opps), return_exceptions=True,
    )
    return [r if not isinstance(r, Exception) else None for r in results]
