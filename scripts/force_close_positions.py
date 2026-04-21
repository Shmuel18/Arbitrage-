#!/usr/bin/env python3
"""
Force-close ALL open positions on all exchanges directly via the exchange API.
Does NOT rely on Redis or bot state — works even for "orphan" positions.

Usage:
    venv\Scripts\python.exe scripts\force_close_positions.py
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import init_config
from src.core.contracts import OrderRequest, OrderSide
from src.exchanges.adapter import ExchangeAdapter


async def close_positions_on(eid: str, adapter: ExchangeAdapter) -> None:
    try:
        positions = await adapter.get_positions()
    except Exception as e:
        print(f"  ❌ [{eid}] Failed to fetch positions: {e}")
        return

    open_pos = [p for p in positions if abs(p.quantity) > Decimal("0")]
    if not open_pos:
        print(f"  ✅ [{eid}] No open positions — nothing to close.")
        return

    for p in open_pos:
        side_str = "LONG" if p.quantity > 0 else "SHORT"
        close_side = OrderSide.SELL if p.quantity > 0 else OrderSide.BUY
        qty = abs(p.quantity)
        print(f"  🔴 [{eid}] Closing {side_str} {qty} {p.symbol} (entry={p.entry_price}) ...")
        try:
            req = OrderRequest(
                exchange=eid,
                symbol=p.symbol,
                side=close_side,
                quantity=qty,
                reduce_only=True,
            )
            order = await adapter.place_order(req)
            filled = order.get("filled") or order.get("amount") or qty
            avg    = order.get("average") or order.get("price") or "?"
            print(f"  ✅ [{eid}] Closed {p.symbol}: filled={filled} @ avg={avg}")
        except Exception as e:
            print(f"  ❌ [{eid}] Failed to close {p.symbol}: {e}")


async def main() -> None:
    cfg = init_config()

    # Connect only the exchanges that have valid keys (skip known broken ones)
    adapters: dict[str, ExchangeAdapter] = {}
    for eid in cfg.enabled_exchanges:
        exc_cfg = cfg.exchanges.get(eid)
        if not exc_cfg:
            continue
        adapters[eid] = ExchangeAdapter(eid, exc_cfg.to_adapter_dict())

    print("🔌 Connecting to exchanges...")
    results = await asyncio.gather(
        *[a.connect() for a in adapters.values()],
        return_exceptions=True,
    )
    connected: dict[str, ExchangeAdapter] = {}
    for eid, result in zip(adapters.keys(), results):
        if isinstance(result, Exception):
            print(f"  ❌ {eid}: {result}")
        else:
            print(f"  ✅ {eid}: connected")
            connected[eid] = adapters[eid]

    print()
    print("🔴 Closing all open positions...")
    print()

    for eid, adapter in connected.items():
        await close_positions_on(eid, adapter)

    print()
    print("🏁 Done.")

    await asyncio.gather(
        *[a.disconnect() for a in connected.values()],
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
