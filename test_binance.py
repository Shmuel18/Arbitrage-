"""Test Binance connection"""
import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('BINANCE_API_KEY')
secret = os.getenv('BINANCE_API_SECRET')

print(f'API Key loaded: {bool(api_key)}')
print(f'Secret loaded: {bool(secret)}')
print(f'API Key (first 10 chars): {api_key[:10] if api_key else "None"}...')

try:
    exchange = ccxt.binanceusdm({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    
    print('\nLoading markets...')
    exchange.load_markets()
    print(f'✅ Markets loaded: {len(exchange.markets)} symbols')
    
    print('\nFetching balance...')
    balance = exchange.fetch_balance()
    usdt = balance.get('USDT', {})
    
    print(f'✅ Binance Futures Connected!')
    print(f'   Free USDT: ${usdt.get("free", 0)}')
    print(f'   Used USDT: ${usdt.get("used", 0)}')
    print(f'   Total USDT: ${usdt.get("total", 0)}')
    
except Exception as e:
    print(f'❌ Error: {e}')
    import traceback
    traceback.print_exc()
