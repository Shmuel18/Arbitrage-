import asyncio, json
import redis.asyncio as aioredis

async def main():
    r = aioredis.from_url("redis://localhost:6379", decode_responses=True)
    keys = await r.keys("trinity:trade:*")
    trade_keys = [k for k in keys if ":history" not in k]
    print(f"{len(trade_keys)} active trades:")
    for k in sorted(trade_keys):
        raw = await r.get(k)
        d = json.loads(raw)
        tid = k.rsplit(":", 1)[-1]
        sym = d["symbol"]
        state = d["state"]
        le = d.get("long_exchange", "?")
        se = d.get("short_exchange", "?")
        lq = d.get("long_qty", "?")
        sq = d.get("short_qty", "?")
        mode = d.get("mode", "?")
        edge = d.get("entry_edge_pct", "?")
        opened = d.get("opened_at", "?")
        print(f"  {tid} | {sym} | {state} | {mode}")
        print(f"    L={le}({lq}) S={se}({sq}) edge={edge}%")
        print(f"    opened={opened}")
    await r.aclose()

asyncio.run(main())
