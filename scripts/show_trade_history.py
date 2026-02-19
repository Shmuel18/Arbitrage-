"""Show closed trade history from Redis."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.redis_client import RedisClient


async def main():
    r = RedisClient()
    await r.connect()

    # Get all closed trades (newest first)
    items = await r._client.zrevrange("trinity:trades:history", 0, -1, withscores=True)
    print(f"Found {len(items)} closed trade(s):\n")

    for raw, score in items:
        trade = json.loads(raw)
        sym = trade.get("symbol", "?")
        long_ex = trade.get("long_exchange", "?")
        short_ex = trade.get("short_exchange", "?")
        pnl = trade.get("total_pnl", 0)
        hold = trade.get("hold_minutes", 0)
        opened = trade.get("opened_at", "?")
        closed = trade.get("closed_at", "?")
        status = trade.get("status", "?")
        long_qty = trade.get("long_qty", "?")
        price_pnl = trade.get("price_pnl", 0)
        funding_net = trade.get("funding_net", 0)
        invested = trade.get("invested", 0)

        print(f"{'='*60}")
        print(f"  {sym}  |  {long_ex} ↔ {short_ex}")
        print(f"  Status: {status}  |  Qty: {long_qty}")
        print(f"  Opened:  {opened}")
        print(f"  Closed:  {closed}")
        print(f"  Hold:    {hold:.1f} min")
        print(f"  Invested: ${invested:.2f}")
        print(f"  Price PnL:   ${price_pnl:.4f}")
        print(f"  Funding Net: ${funding_net:.4f}")
        print(f"  NET PROFIT:  ${pnl:.4f}")

    # Also check active trades
    keys = await r._client.keys("trinity:trade:*")
    if keys:
        print(f"\n{'='*60}")
        print(f"Active trades in Redis: {len(keys)}")
        for key in keys:
            data = await r._client.get(key)
            if data:
                t = json.loads(data)
                print(f"  - {t.get('symbol','?')} ({t.get('long_exchange','?')} ↔ {t.get('short_exchange','?')}) state={t.get('state','?')}")

    await r.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
