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

# P3-4/P3-5: Module-level constants — never recreated per compute() call.
_DEFAULT_LEVERAGE: int = 5          # fallback when exchange config omits leverage
_MIN_BALANCE_USD: Decimal = Decimal("5")  # below this, entry risks immediate liquidation
_ZERO: Decimal = Decimal("0")
_ONE: Decimal = Decimal("1")
_FALLBACK_LOT: Decimal = Decimal("0.001")  # last-resort lot step when spec is missing


class PositionSizer:
    """Compute a harmonised order quantity for both legs of a funding-arb trade.

    Responsibilities:
    - Fetch free balances from both exchanges.
    - Apply ``position_size_pct × leverage`` to the *smaller* balance.
    - Round down qty to the coarsest lot step found across both exchanges.
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
        long_bal, short_bal, _live_ticker = await asyncio.gather(
            long_adapter.get_balance(),
            short_adapter.get_balance(),
            # P1-3: Fetch a live price in parallel with balances so qty is computed
            # against the CURRENT market price, not the scanner snapshot which can be
            # 300ms–60s stale.  Actual fill notional = qty × fill_price; using a stale
            # reference_price silently bypasses max_position_size_usd on fast-moving assets.
            long_adapter.get_ticker(opp.symbol),
        )

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

        # P1-3: Cross-margin amplification guard.
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
        notional = min_balance * position_pct * lev

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

        # P1-3: Use live ticker price when available; fall back to opp.reference_price
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

        logger.info(
            f"{opp.symbol}: Qty \u2014 "
            f"notional=${notional:.2f} / ${_price_for_sizing:.4f} = "
            f"{qty_raw:.4f} tokens, lot_base={lot} "
            f"(L:{long_lot_base}/S:{short_lot_base}), "
            f"L_cs={long_cs} S_cs={short_cs}, order_qty={order_qty}"
        )

        return order_qty, notional, long_spec, short_spec
