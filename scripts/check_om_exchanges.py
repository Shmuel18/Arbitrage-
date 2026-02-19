"""Check if OM/USDT:USDT exists on each exchange and what rate they report."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress log noise
import logging
logging.disable(logging.CRITICAL)

from src.core.config import init_config
from src.exchanges.adapter import ExchangeManager

async def main():
    cfg = init_config()
    mgr = ExchangeManager()
    await mgr.connect_all()
    
    adapters = mgr.all()
    symbol = "OM/USDT:USDT"
    
    print(f"Checking {symbol} on {len(adapters)} exchanges...", flush=True)
    
    for eid, adapter in adapters.items():
        has_symbol = symbol in adapter._exchange.symbols
        print(f"{eid:12s}: symbol exists = {has_symbol}", flush=True)
        if has_symbol:
            cached = adapter.get_funding_rate_cached(symbol)
            if cached:
                rate = cached.get("rate", "?")
                interval = cached.get("interval_hours", "?")
                print(f"             cached rate={rate} interval={interval}h", flush=True)
            else:
                print(f"             no cached rate", flush=True)
            try:
                data = await adapter.get_funding_rate(symbol)
                if data:
                    print(f"             REST  rate={data.get('rate')} interval={data.get('interval_hours')}h next_ts={data.get('next_timestamp')}", flush=True)
                else:
                    print(f"             REST  returned None", flush=True)
            except Exception as e:
                print(f"             REST  error: {e}", flush=True)
    
    await mgr.disconnect_all()

asyncio.run(main())
