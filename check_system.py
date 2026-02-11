"""
Quick system check - verifies all components are ready for live trading
Run this before starting the bot!
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


async def main():
    print("=" * 70)
    print("🔍 TRINITY BOT - SYSTEM CHECK")
    print("=" * 70)
    
    all_ok = True
    
    # ========== 1. Redis ==========
    print("\n📦 [1/4] REDIS...")
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, socket_timeout=3)
        r.ping()
        print("   ✅ Redis is running!")
    except Exception as e:
        print(f"   ❌ Redis is NOT running: {e}")
        print("   💡 Fix: docker-compose up -d redis")
        all_ok = False

    # ========== 2. API Keys ==========
    print("\n🔑 [2/4] API KEYS...")
    exchanges_config = {
        'BINANCE': {'key': os.getenv('BINANCE_API_KEY'), 'secret': os.getenv('BINANCE_API_SECRET')},
        'BYBIT': {'key': os.getenv('BYBIT_API_KEY'), 'secret': os.getenv('BYBIT_API_SECRET')},
        'GATEIO': {'key': os.getenv('GATEIO_API_KEY'), 'secret': os.getenv('GATEIO_API_SECRET')},
        'OKX': {'key': os.getenv('OKX_API_KEY'), 'secret': os.getenv('OKX_API_SECRET')},
    }
    
    configured = 0
    for name, creds in exchanges_config.items():
        key = creds['key']
        if key and len(str(key).strip()) > 10:
            print(f"   ✅ {name}: Key configured ({key[:10]}...)")
            configured += 1
        else:
            print(f"   ⚠️  {name}: No API key set")
    
    if configured < 2:
        print(f"   ❌ Need at least 2 exchanges! Only {configured} configured.")
        all_ok = False
    else:
        print(f"   ✅ {configured} exchanges configured")
    
    # ========== 3. Exchange Connectivity ==========
    print("\n📡 [3/4] EXCHANGE CONNECTIVITY...")
    try:
        import ccxt
        
        exchange_tests = []
        
        if os.getenv('BINANCE_API_KEY') and len(str(os.getenv('BINANCE_API_KEY')).strip()) > 10:
            exchange_tests.append(('binanceusdm', ccxt.binanceusdm, {
                'apiKey': os.getenv('BINANCE_API_KEY'),
                'secret': os.getenv('BINANCE_API_SECRET'),
            }))
        
        if os.getenv('BYBIT_API_KEY') and len(str(os.getenv('BYBIT_API_KEY')).strip()) > 10:
            exchange_tests.append(('bybit', ccxt.bybit, {
                'apiKey': os.getenv('BYBIT_API_KEY'),
                'secret': os.getenv('BYBIT_API_SECRET'),
                'options': {'defaultType': 'linear'},
            }))
        
        if os.getenv('GATEIO_API_KEY') and len(str(os.getenv('GATEIO_API_KEY')).strip()) > 10:
            exchange_tests.append(('gate', ccxt.gate, {
                'apiKey': os.getenv('GATEIO_API_KEY'),
                'secret': os.getenv('GATEIO_API_SECRET'),
            }))
        
        if os.getenv('OKX_API_KEY') and len(str(os.getenv('OKX_API_KEY')).strip()) > 10:
            exchange_tests.append(('okx', ccxt.okx, {
                'apiKey': os.getenv('OKX_API_KEY'),
                'secret': os.getenv('OKX_API_SECRET'),
                'password': os.getenv('OKX_PASSPHRASE'),
            }))
        
        connected = 0
        total_balance = 0.0
        
        for name, cls, params in exchange_tests:
            try:
                ex = cls({**params, 'enableRateLimit': True})
                ex.load_markets()
                balance = ex.fetch_balance()
                usdt = float(balance.get('USDT', {}).get('free', 0) or 0)
                total_balance += usdt
                print(f"   ✅ {name}: Connected! USDT = ${usdt:.2f}")
                connected += 1
            except Exception as e:
                err_msg = str(e)[:80]
                print(f"   ❌ {name}: {err_msg}")
                all_ok = False
        
        if connected < 2:
            print(f"   ❌ Need at least 2 connected exchanges! Only {connected} working.")
            all_ok = False
        else:
            print(f"   ✅ {connected} exchanges connected | Total USDT: ${total_balance:.2f}")
            
    except ImportError:
        print("   ❌ ccxt not installed: pip install ccxt")
        all_ok = False
    
    # ========== 4. Config ==========
    print("\n⚙️  [4/4] CONFIGURATION...")
    paper = os.getenv('PAPER_TRADING', 'true').lower()
    dry = os.getenv('DRY_RUN', 'true').lower()
    
    if paper == 'false' and dry == 'false':
        print("   🔴 MODE: LIVE TRADING (real money!)")
    elif paper == 'true':
        print("   🟢 MODE: Paper Trading (simulation)")
    else:
        print("   🟡 MODE: Dry Run (simulation)")
    
    # ========== SUMMARY ==========
    print(f"\n{'=' * 70}")
    if all_ok:
        print("🎉 ALL CHECKS PASSED! Bot is ready to run.")
        print(f"\n   Start the bot:")
        print(f"   python main.py")
    else:
        print("⚠️  SOME CHECKS FAILED - Fix the issues above before running.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
