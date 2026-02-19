"""Inspect and optionally clean stale trade entries from Redis."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.redis_client import RedisClient


async def main():
    r = RedisClient()
    await r.connect()

    keys = await r._client.keys("trinity:trade:*")
    to_delete = []

    for k in sorted(keys):
        d = await r._client.get(k)
        if not d:
            continue
        t = json.loads(d)
        state = t.get("state", "?")
        symbol = t.get("symbol", "?")
        long_ex = t.get("long_exchange", "?")
        short_ex = t.get("short_exchange", "?")
        opened = t.get("opened_at", "?")

        print(f"\n=== {k} ===")
        print(f"  symbol:    {symbol}")
        print(f"  state:     {state}")
        print(f"  long:      {long_ex} qty={t.get('long_qty', '?')}")
        print(f"  short:     {short_ex} qty={t.get('short_qty', '?')}")
        print(f"  opened_at: {opened}")
        if t.get("error"):
            print(f"  error:     {t.get('error')}")

        if state == "error":
            to_delete.append(k)

    if to_delete:
        print(f"\n{'='*60}")
        print(f"Deleting {len(to_delete)} error-state trades...")
        for k in to_delete:
            await r._client.delete(k)
            print(f"  DELETED: {k}")
        print("Done!")
    else:
        print("\nNo error-state trades to clean.")

    # Show remaining
    remaining = await r._client.keys("trinity:trade:*")
    print(f"\nRemaining active trades: {len(remaining)}")
    for k in sorted(remaining):
        d = await r._client.get(k)
        if d:
            t = json.loads(d)
            print(f"  - {t.get('symbol','?')} state={t.get('state','?')}")

    await r.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
