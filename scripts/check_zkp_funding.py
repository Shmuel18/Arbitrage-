"""Check ZKP/USDT funding rates on Bybit and Binance."""
import asyncio
import ccxt.pro as ccxtpro

async def check():
    bybit = ccxtpro.bybit()
    binance = ccxtpro.binanceusdm()
    
    print('=== ZKP/USDT:USDT FUNDING RATES ===\n')
    
    try:
        b_funding = await bybit.fetch_funding_rate('ZKP/USDT:USDT')
        by_rate = float(b_funding['fundingRate'])
        by_next = b_funding.get('fundingTimestamp', 0) / 1000
        print(f"Bybit:   rate = {by_rate:.8f} ({by_rate*100:.6f}%)")
    except Exception as e:
        print(f"Bybit error: {e}")
        by_rate = 0
    
    try:
        bn_funding = await binance.fetch_funding_rate('ZKP/USDT:USDT')
        bn_rate = float(bn_funding['fundingRate'])
        bn_next = bn_funding.get('fundingTimestamp', 0) / 1000
        print(f"Binance: rate = {bn_rate:.8f} ({bn_rate*100:.6f}%)")
    except Exception as e:
        print(f"Binance error: {e}")
        bn_rate = 0
    
    # Calculate gross spread
    spread_pct = (by_rate - bn_rate) * 100
    print(f"\n📊 GROSS SPREAD = Bybit - Binance")
    print(f"   = {by_rate:.8f} - ({bn_rate:.8f})")
    print(f"   = {spread_pct:.4f}%")
    
    # For 8-hour to 24-hour accumulation
    print(f"\n💰 ACCUMULATED SPREAD:")
    print(f"   8 hours:  {spread_pct:.4f}%")
    print(f"   16 hours: {spread_pct*2:.4f}%")
    print(f"   24 hours: {spread_pct*3:.4f}%")
    
    await bybit.close()
    await binance.close()

asyncio.run(check())
