"""
Position sizer — compute order quantity for a funding-arb entry.

Extracted from ExecutionController to keep sizing logic independently
testable and free of side effects (except the async balance/spec fetches).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.config import Config
    from src.core.contracts import InstrumentSpec, OpportunityCandidate
    from src.exchanges.adapter import ExchangeAdapter

logger = get_logger("sizer")

# Module-level constants — never recreated per compute() call.
_DEFAULT_LEVERAGE: int = 5          # fallback when exchange config omits leverage
_MIN_BALANCE_USD: Decimal = Decimal("5")   # below this, entry risks immediate liquidation
_MARGIN_SAFETY: Decimal = Decimal("0.90")  # never use more than 90% of free balance as margin
_ZERO: Decimal = Decimal("0")
_ONE: Decimal = Decimal("1")
_FALLBACK_LOT: Decimal = Decimal("0.001")  # last-resort lot step when spec is missing


class PositionSizer:
    """Compute a harmonised order quantity for both legs of a funding-arb trade.

    Responsibilities:
    - Fetch free balances from both exchanges.
    - Apply ``position_size_pct × leverage`` to the *smaller* balance.
    - Round down qty to the coarsest lot step found across both exchanges.
    - Validate that the computed margin does not exceed the available free balance
      (guards against exchanges with non-standard initial margin rates).
    - Return the final quantity along with the instrument specs used.

    This class is *stateless between calls* — create one instance and reuse it.
    """

    def __init__(self, cfg: "Config") -> None:
        self._cfg = cfg

    async def compute(
        self,
        opp: "OpportunityCandidate",
        long_adapter: "ExchangeAdapter",
        short_adapter: "ExchangeAdapter",
    ) -> Optional[Tuple[Decimal, Decimal, "InstrumentSpec", "InstrumentSpec"]]:
        """Compute order quantity and return ``(qty, notional, long_spec, short_spec)``.

        Returns ``None`` if sizing is not possible (zero balance, missing spec, etc.).
        """
        # Balances: cached snapshot (≤ 3 s old) is good enough for sizing.
        # status_publisher polls balances on a tight loop for the dashboard,
        # so the cache is usually warm by the time an entry fires — this
        # eliminates the ~300-700 ms REST round-trip per leg from the
        # entry hot-path. The 90 % margin-safety cap downstream still
        # protects against any short-term staleness.
        long_bal, short_bal = await asyncio.gather(
            long_adapter.get_balance_cached(max_age_sec=3.0),
            short_adapter.get_balance_cached(max_age_sec=3.0),
        )
        # Skip the live ticker fetch — opp.reference_price is the scanner's
        # mark/last snapshot from < 5 s ago and good enough for qty sizing
        # (the order is a market fill anyway; the price affects qty calc,
        # not the actual execution price). Saves another ~300-500 ms per
        # entry. The margin-safety + min_lot validations downstream catch
        # any bad sizing from a stale price.
        _live_ticker = None

        position_pct = Decimal(str(self._cfg.risk_limits.position_size_pct))

        long_exc_cfg = self._cfg.exchanges.get(opp.long_exchange)
        short_exc_cfg = self._cfg.exchanges.get(opp.short_exchange)
        lev = int(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else _DEFAULT_LEVERAGE)
        lev_short = int(short_exc_cfg.leverage if short_exc_cfg and short_exc_cfg.leverage else _DEFAULT_LEVERAGE)
        if lev != lev_short:
            logger.warning(
                f"Leverage mismatch: {opp.long_exchange}={lev}x vs "
                f"{opp.short_exchange}={lev_short}x — using min"
            )
            lev = min(lev, lev_short)

        long_free = Decimal(str(long_bal["free"]))
        short_free = Decimal(str(short_bal["free"]))
        long_total = Decimal(str(long_bal.get("total") or long_free))
        short_total = Decimal(str(short_bal.get("total") or short_free))

        # Cross-margin amplification guard.
        # On cross-margin accounts (default for perp desks), opening a new position
        # increases maintenance margin for ALL positions non-linearly. Checking only
        # `free` balance misses the case where existing trades already consume most
        # of the equity. If current margin usage exceeds max_margin_usage, skip entry.
        _max_usage = Decimal(str(self._cfg.risk_limits.max_margin_usage))
        if long_total > _ZERO:
            _long_usage = (long_total - long_free) / long_total
            if _long_usage >= _max_usage:
                logger.warning(
                    f"{opp.symbol}: Skipping — {opp.long_exchange} margin usage "
                    f"{float(_long_usage * 100):.1f}% >= max {float(_max_usage * 100):.1f}% "
                    f"(total=${long_total:.2f} free=${long_free:.2f})"
                )
                return None
        if short_total > _ZERO:
            _short_usage = (short_total - short_free) / short_total
            if _short_usage >= _max_usage:
                logger.warning(
                    f"{opp.symbol}: Skipping — {opp.short_exchange} margin usage "
                    f"{float(_short_usage * 100):.1f}% >= max {float(_max_usage * 100):.1f}% "
                    f"(total=${short_total:.2f} free=${short_free:.2f})"
                )
                return None

        # Minimum balance guard — don't enter if either exchange has less
        # than _MIN_BALANCE_USD free.  Tiny balances lead to immediate liquidation
        # risk exits (margin_ratio drops below safety threshold on first adverse tick).
        if long_free < _MIN_BALANCE_USD:
            logger.warning(
                f"{opp.symbol}: Skipping — {opp.long_exchange} balance "
                f"${long_free:.2f} < min ${_MIN_BALANCE_USD:.0f}"
            )
            return None
        if short_free < _MIN_BALANCE_USD:
            logger.warning(
                f"{opp.symbol}: Skipping — {opp.short_exchange} balance "
                f"${short_free:.2f} < min ${_MIN_BALANCE_USD:.0f}"
            )
            return None

        min_balance = min(long_free, short_free)
        notional = min_balance * position_pct * Decimal(str(lev))

        logger.info(
            f"{opp.symbol}: Sizing — "
            f"L={opp.long_exchange}=${long_free:.2f} S={opp.short_exchange}=${short_free:.2f} "
            f"min_bal=${min_balance:.2f} × {int(position_pct*100)}% × {lev}x = "
            f"${notional:.2f} notional"
        )

        if notional <= 0:
            logger.warning(f"Insufficient balance for {opp.symbol}")
            return None

        long_spec, short_spec = await asyncio.gather(
            long_adapter.get_instrument_spec(opp.symbol),
            short_adapter.get_instrument_spec(opp.symbol),
        )

        long_cs = Decimal(str(long_spec.contract_size)) if long_spec and long_spec.contract_size else _ONE
        short_cs = Decimal(str(short_spec.contract_size)) if short_spec and short_spec.contract_size else _ONE
        long_lot_base = (Decimal(str(long_spec.lot_size)) * long_cs) if long_spec else _FALLBACK_LOT
        short_lot_base = (Decimal(str(short_spec.lot_size)) * short_cs) if short_spec else _FALLBACK_LOT
        lot = max(long_lot_base, short_lot_base)
        if lot <= 0:
            logger.warning(f"{opp.symbol}: lot_size resolved to zero, falling back to {_FALLBACK_LOT}")
            lot = _FALLBACK_LOT

        # Use live ticker price when available; fall back to opp.reference_price
        # if the ticker returned an empty or zero value (e.g. during exchange maintenance).
        _live_price = Decimal(str(_live_ticker.get("last", 0) or 0)) if _live_ticker else _ZERO
        _price_for_sizing = _live_price if _live_price > _ZERO else opp.reference_price

        qty_raw = notional / _price_for_sizing
        steps = int(qty_raw / lot)
        if steps == 0:
            logger.warning(
                f"{opp.symbol}: Skipping — calculated qty {qty_raw:.4f} is below "
                f"minimum lot {lot} (notional=${notional:.2f}, price=${_price_for_sizing:.4f}). "
                f"Need at least ${float(lot * _price_for_sizing / Decimal(str(lev))):.2f} free on "
                f"{opp.short_exchange} to open 1 lot."
            )
            return None

        qty_rounded = Decimal(str(round(float(steps * lot), 8)))
        order_qty = qty_rounded

        # Margin safety validation — some exchanges (e.g. GateIO) apply higher
        # initial margin rates for volatile or illiquid tokens, meaning the actual
        # margin required can exceed notional / leverage.  Guard against this by
        # ensuring the estimated margin (qty × price / lev) stays within
        # _MARGIN_SAFETY × the smaller free balance.  If it doesn't, scale down
        # qty by one lot at a time until it fits (or bail if we fall below 1 lot).
        _lev_dec = Decimal(str(lev))
        _max_margin = min(long_free, short_free) * _MARGIN_SAFETY
        _estimated_margin = order_qty * _price_for_sizing / _lev_dec
        if _estimated_margin > _max_margin:
            _safe_steps = int((_max_margin * _lev_dec / _price_for_sizing) / lot)
            if _safe_steps == 0:
                logger.warning(
                    f"{opp.symbol}: Skipping — margin safety cap: estimated margin "
                    f"${float(_estimated_margin):.2f} > safe limit ${float(_max_margin):.2f} "
                    f"(free=${float(min(long_free, short_free)):.2f}, lev={lev}x). "
                    f"Even 1 lot requires ${float(lot * _price_for_sizing / _lev_dec):.2f}."
                )
                return None
            qty_rounded = Decimal(str(round(float(_safe_steps * lot), 8)))
            order_qty = qty_rounded
            notional = order_qty * _price_for_sizing
            logger.warning(
                f"{opp.symbol}: Qty scaled down to {order_qty} lots "
                f"(margin safety cap: {float(_estimated_margin):.2f} > {float(_max_margin):.2f}). "
                f"New notional=${float(notional):.2f}"
            )

        logger.info(
            f"{opp.symbol}: Qty \u2014 "
            f"notional=${float(notional):.2f} / ${float(_price_for_sizing):.4f} = "
            f"{float(qty_raw):.4f} tokens, lot_base={lot} "
            f"(L:{long_lot_base}/S:{short_lot_base}), "
            f"L_cs={long_cs} S_cs={short_cs}, order_qty={order_qty}"
        )

        return order_qty, notional, long_spec, short_spec
