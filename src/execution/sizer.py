"""
Position sizer — compute order quantity for a funding-arb entry.

Extracted from ExecutionController to keep sizing logic independently
testable and free of side effects (except the async balance/spec fetches).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.config import Config
    from src.core.contracts import InstrumentSpec, OpportunityCandidate
    from src.exchanges.adapter import ExchangeAdapter

logger = get_logger("sizer")


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
        long_bal = await long_adapter.get_balance()
        short_bal = await short_adapter.get_balance()

        position_pct = float(self._cfg.risk_limits.position_size_pct)

        long_exc_cfg = self._cfg.exchanges.get(opp.long_exchange)
        short_exc_cfg = self._cfg.exchanges.get(opp.short_exchange)
        lev = int(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else 5)
        lev_short = int(short_exc_cfg.leverage if short_exc_cfg and short_exc_cfg.leverage else 5)
        if lev != lev_short:
            logger.warning(
                f"Leverage mismatch: {opp.long_exchange}={lev}x vs "
                f"{opp.short_exchange}={lev_short}x — using min"
            )
            lev = min(lev, lev_short)

        long_free = float(long_bal["free"])
        short_free = float(short_bal["free"])
        min_balance = min(long_free, short_free)
        notional = Decimal(str(min_balance * position_pct * lev))

        logger.info(
            f"{opp.symbol}: Sizing — "
            f"L={opp.long_exchange}=${long_free:.2f} S={opp.short_exchange}=${short_free:.2f} "
            f"min_bal=${min_balance:.2f} × {int(position_pct*100)}% × {lev}x = "
            f"${float(notional):.2f} notional"
        )

        if notional <= 0:
            logger.warning(f"Insufficient balance for {opp.symbol}")
            return None

        long_spec = await long_adapter.get_instrument_spec(opp.symbol)
        short_spec = await short_adapter.get_instrument_spec(opp.symbol)

        long_cs = float(long_spec.contract_size) if long_spec and long_spec.contract_size else 1.0
        short_cs = float(short_spec.contract_size) if short_spec and short_spec.contract_size else 1.0
        long_lot_base = (float(long_spec.lot_size) * long_cs) if long_spec else 0.001
        short_lot_base = (float(short_spec.lot_size) * short_cs) if short_spec else 0.001
        lot = max(long_lot_base, short_lot_base)

        qty_float = float(notional / opp.reference_price)
        steps = int(qty_float / lot)
        qty_rounded = round(steps * lot, 8)
        qty_rounded = max(qty_rounded, lot)
        order_qty = Decimal(str(qty_rounded))

        logger.info(
            f"{opp.symbol}: Qty — "
            f"notional=${float(notional):.2f} / ${float(opp.reference_price):.4f} = "
            f"{qty_float:.4f} tokens, lot_base={lot} "
            f"(L:{long_lot_base}/S:{short_lot_base}), "
            f"L_cs={long_cs} S_cs={short_cs}, order_qty={order_qty}"
        )

        return order_qty, notional, long_spec, short_spec
