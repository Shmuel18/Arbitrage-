"""Diagnostic: compare POWER/USDT funding rates from exchange APIs."""
import asyncio
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

async def check():
    import ccxt.pro as ccxt

    symbol = "POWER/USDT:USDT"

    # Check Bitget
    bitget = ccxt.bitget({
        "apiKey": os.getenv("BITGET_API_KEY", ""),
        "secret": os.getenv("BITGET_SECRET", ""),
        "password": os.getenv("BITGET_PASSWORD", ""),
    })
    try:
        data = await bitget.fetch_funding_rate(symbol)
        print("=== BITGET POWER/USDT:USDT ===")
        print(f"  fundingRate:          {data.get('fundingRate')}")
        print(f"  fundingTimestamp:      {data.get('fundingTimestamp')}")
        print(f"  nextFundingTimestamp:  {data.get('nextFundingTimestamp')}")
        print(f"  datetime:             {data.get('datetime')}")
        print(f"  interval:             {data.get('interval')}")
        info = data.get("info", {})
        print(f"  info keys: {sorted(info.keys()) if info else 'N/A'}")
        print(f"  Full info: {json.dumps(info, indent=4, default=str)}")
    except Exception as e:
        print(f"Bitget error: {e}")
    finally:
        await bitget.close()

    print()

    # Check KuCoin
    kucoin = ccxt.kucoinfutures({
        "apiKey": os.getenv("KUCOIN_API_KEY", ""),
        "secret": os.getenv("KUCOIN_SECRET", ""),
        "password": os.getenv("KUCOIN_PASSWORD", ""),
    })
    try:
        data = await kucoin.fetch_funding_rate(symbol)
        print("=== KUCOIN POWER/USDT:USDT ===")
        print(f"  fundingRate:          {data.get('fundingRate')}")
        print(f"  fundingTimestamp:      {data.get('fundingTimestamp')}")
        print(f"  nextFundingTimestamp:  {data.get('nextFundingTimestamp')}")
        print(f"  datetime:             {data.get('datetime')}")
        print(f"  interval:             {data.get('interval')}")
        info = data.get("info", {})
        print(f"  info keys: {sorted(info.keys()) if info else 'N/A'}")
        print(f"  Full info: {json.dumps(info, indent=4, default=str)}")
    except Exception as e:
        print(f"KuCoin error: {e}")
    finally:
        await kucoin.close()

    print()

    # Check Binance
    binance = ccxt.binanceusdm({
        "apiKey": os.getenv("BINANCE_API_KEY", ""),
        "secret": os.getenv("BINANCE_SECRET", ""),
    })
    try:
        data = await binance.fetch_funding_rate(symbol)
        print("=== BINANCE POWER/USDT:USDT ===")
        print(f"  fundingRate:          {data.get('fundingRate')}")
        print(f"  fundingTimestamp:      {data.get('fundingTimestamp')}")
        print(f"  nextFundingTimestamp:  {data.get('nextFundingTimestamp')}")
        print(f"  datetime:             {data.get('datetime')}")
        print(f"  interval:             {data.get('interval')}")
    except Exception as e:
        print(f"Binance error: {e}")
    finally:
        await binance.close()

asyncio.run(check())
