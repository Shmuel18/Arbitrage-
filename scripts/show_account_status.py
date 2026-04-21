#!/usr/bin/env python3
"""
Show full account status: balances, open positions, and margin usage per exchange.
Connects directly to the exchange APIs — no Redis / bot state needed.
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import init_config
from src.exchanges.adapter import ExchangeAdapter, ExchangeManager


async def main() -> None:
    cfg = init_config()

    adapters: dict[str, ExchangeAdapter] = {}
    for eid in cfg.enabled_exchanges:
        exc_cfg = cfg.exchanges.get(eid)
        if not exc_cfg:
            continue
        adapters[eid] = ExchangeAdapter(eid, exc_cfg.to_adapter_dict())

    # Connect all in parallel
    results = await asyncio.gather(
        *[a.connect() for a in adapters.values()],
        return_exceptions=True,
    )
    connected: dict[str, ExchangeAdapter] = {}
    for eid, result in zip(adapters.keys(), results):
        if isinstance(result, Exception):
            print(f"❌ {eid.upper()}: failed to connect — {result}")
        else:
            connected[eid] = adapters[eid]

    print()
    print("=" * 60)
    print("  ACCOUNT STATUS")
    print("=" * 60)

    total_equity = Decimal("0")

    for eid, adapter in connected.items():
        print(f"\n{'─'*60}")
        print(f"  {eid.upper()}")
        print(f"{'─'*60}")

        # Balance
        try:
            bal = await adapter.get_balance()
            total  = Decimal(str(bal.get("total", 0) or 0))
            free   = Decimal(str(bal.get("free",  0) or 0))
            used   = Decimal(str(bal.get("used",  0) or 0))
            total_equity += total
            pct_used = (used / total * 100) if total else Decimal("0")
            print(f"  Balance  : ${total:.2f} total  |  ${free:.2f} free  |  ${used:.2f} used ({pct_used:.1f}%)")
        except Exception as e:
            print(f"  Balance  : ERROR — {e}")

        # Positions
        try:
            positions = await adapter.get_positions()
            open_pos = [p for p in positions if abs(p.quantity) > Decimal("0")]
            if not open_pos:
                print("  Positions: ✅ None (account fully flat)")
            else:
                print(f"  Positions: {len(open_pos)} open position(s)")
                for p in open_pos:
                    side     = "LONG " if p.quantity > 0 else "SHORT"
                    pnl      = f"  PnL={p.unrealized_pnl:+.4f}" if p.unrealized_pnl is not None else ""
                    notional = abs(p.quantity) * p.entry_price if p.entry_price else Decimal("0")
                    print(f"    [{side}] {p.symbol:30s}  qty={p.quantity}  entry={p.entry_price}  notional≈${notional:.2f}{pnl}")
        except Exception as e:
            print(f"  Positions: ERROR — {e}")

    print()
    print("=" * 60)
    print(f"  TOTAL EQUITY  :  ${total_equity:.2f}")
    print("=" * 60)
    print()

    # Cleanup
    await asyncio.gather(
        *[a.disconnect() for a in connected.values()],
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
