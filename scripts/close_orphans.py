"""
Emergency script to close all open positions on all exchanges.
Run: python scripts/close_orphans.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from src.core.config import get_config
from src.exchanges.adapter import ExchangeAdapter


async def main():
    config = get_config()
    adapters = {}

    # Connect to exchanges
    for name, exc_cfg in config.exchanges.items():
        if not exc_cfg.api_key or not exc_cfg.api_secret:
            print(f"⏭️  Skipping {name} — no credentials")
            continue
        cfg_dict = {
            "ccxt_id": exc_cfg.ccxt_id or name,
            "api_key": exc_cfg.api_key,
            "api_secret": exc_cfg.api_secret,
            "api_passphrase": exc_cfg.api_passphrase,
            "default_type": "swap",
            "leverage": exc_cfg.leverage or 5,
            "margin_mode": exc_cfg.margin_mode or "cross",
            "position_mode": exc_cfg.position_mode or "oneway",
        }
        adapter = ExchangeAdapter(name, cfg_dict)
        try:
            await adapter.connect()
            ok = await adapter.verify_credentials()
            if not ok:
                print(f"❌ {name} — credentials invalid, skipping")
                await adapter.disconnect()
                continue
            adapters[name] = adapter
            print(f"✅ Connected to {name}")
        except Exception as e:
            print(f"❌ {name} — failed to connect: {e}")

    if not adapters:
        print("No exchanges connected!")
        return

    # Check all positions
    total_positions = 0
    for name, adapter in adapters.items():
        try:
            positions = await adapter.get_positions()
            if positions:
                print(f"\n📊 {name} has {len(positions)} open position(s):")
                for pos in positions:
                    print(f"   {pos.symbol}  side={pos.side.value}  qty={pos.quantity}  entry={pos.entry_price}  pnl={pos.unrealized_pnl}")
                    total_positions += 1
            else:
                print(f"\n✅ {name} — no open positions")
        except Exception as e:
            print(f"\n❌ {name} — error fetching positions: {e}")

    if total_positions == 0:
        print("\n✅ No open positions found on any exchange!")
        # Show balances
        for name, adapter in adapters.items():
            try:
                bal = await adapter.get_balance()
                print(f"   {name}: {bal['total']:.4f} USDT (free={bal['free']:.4f})")
            except:
                pass
    else:
        print(f"\n⚠️  Found {total_positions} open position(s) total!")
        confirm = input("Close ALL positions? (type YES): ").strip()
        if confirm == "YES":
            from src.core.contracts import OrderRequest, OrderSide
            for name, adapter in adapters.items():
                positions = await adapter.get_positions()
                for pos in positions:
                    close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
                    order = OrderRequest(
                        exchange=name,
                        symbol=pos.symbol,
                        side=close_side,
                        quantity=pos.quantity,
                        reduce_only=True,
                    )
                    try:
                        result = await adapter.place_order(order)
                        print(f"   ✅ Closed {pos.symbol} on {name}: {close_side.value} {pos.quantity}")
                    except Exception as e:
                        print(f"   ❌ Failed to close {pos.symbol} on {name}: {e}")
            print("\n✅ Done closing positions!")
        else:
            print("Cancelled.")

    # Disconnect
    for adapter in adapters.values():
        try:
            await adapter.disconnect()
        except:
            pass


if __name__ == "__main__":
    asyncio.run(main())
