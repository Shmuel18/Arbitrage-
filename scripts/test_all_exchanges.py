"""Test all exchange connections"""
import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

# Test configuration
EXCHANGES = {
    'binanceusdm': {
        'class': ccxt.binanceusdm,
        'api_key': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'options': {'defaultType': 'future'}
    },
    'bybit': {
        'class': ccxt.bybit,
        'api_key': os.getenv('BYBIT_API_KEY'),
        'secret': os.getenv('BYBIT_API_SECRET'),
        'options': {'defaultType': 'linear'}
    },
    'gate': {
        'class': ccxt.gate,
        'api_key': os.getenv('GATEIO_API_KEY'),
        'secret': os.getenv('GATEIO_API_SECRET'),
        'options': {}
    },
    'okx': {
        'class': ccxt.okx,
        'api_key': os.getenv('OKX_API_KEY'),
        'secret': os.getenv('OKX_API_SECRET'),
        'password': os.getenv('OKX_PASSPHRASE'),
        'options': {}
    }
}

print("="*70)
print("🔍 בודק חיבור לכל הבורסאות")
print("="*70)

results = {}

for name, config in EXCHANGES.items():
    print(f"\n{'='*70}")
    print(f"📡 {name.upper()}")
    print('='*70)
    
    api_key = config['api_key']
    secret = config['secret']
    
    # Check if keys exist
    if not api_key or len(str(api_key).strip()) < 10:
        print(f"⚠️  מפתח API לא נמצא או קצר מדי")
        results[name] = '❌ חסר מפתח'
        continue
        
    if 'הדבק' in str(api_key) or str(api_key) == 'None':
        print(f"⚠️  מפתח API לא מוגדר (מכיל טקסט ברירת מחדל)")
        results[name] = '❌ לא מוגדר'
        continue
    
    print(f"   API Key: {api_key[:15]}...")
    print(f"   Secret: {secret[:10] if secret else 'None'}...")
    
    try:
        # Initialize exchange
        params = {
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': config['options']
        }
        
        if 'password' in config and config['password']:
            params['password'] = config['password']
        
        exchange = config['class'](params)
        
        # Try to load markets
        print(f"   📊 טוען שווקים...")
        markets = exchange.load_markets()
        print(f"   ✅ שווקים נטענו: {len(markets)} סמלים")
        
        # Try to fetch balance
        print(f"   💰 מביא יתרה...")
        balance = exchange.fetch_balance()
        
        # Get USDT balance
        usdt = balance.get('USDT', {}) or balance.get('USDT', {'free': 0, 'total': 0})
        free = usdt.get('free', 0) or 0
        total = usdt.get('total', 0) or 0
        
        print(f"   ✅ מחובר בהצלחה!")
        print(f"   💵 USDT חופשי: ${free:.2f}")
        print(f"   💵 USDT סה\"כ: ${total:.2f}")
        
        results[name] = f'✅ ${free:.2f}'
        
    except ccxt.AuthenticationError as e:
        print(f"   ❌ שגיאת הזדהות: {e}")
        results[name] = '❌ מפתח שגוי'
        
    except ccxt.InsufficientPermissions as e:
        print(f"   ❌ אין הרשאות מספיקות: {e}")
        results[name] = '❌ חסרות הרשאות'
        
    except Exception as e:
        print(f"   ❌ שגיאה: {type(e).__name__}: {str(e)[:100]}")
        results[name] = f'❌ {type(e).__name__}'

# Summary
print(f"\n\n{'='*70}")
print("📊 סיכום")
print('='*70)
print(f"{'בורסה':<20} | {'סטטוס'}")
print('-'*70)
for name, status in results.items():
    print(f"{name:<20} | {status}")
print('='*70)

# Count successes
successes = sum(1 for s in results.values() if '✅' in s)
print(f"\n✅ מחוברות: {successes}/{len(results)}")
print(f"❌ לא מחוברות: {len(results) - successes}/{len(results)}")

if successes == 0:
    print("\n⚠️  אף בורסה לא מחוברת! בדוק את המפתחות ב-.env")
elif successes < len(results):
    print("\n⚠️  חלק מהבורסאות לא מחוברות. בדוק את BINANCE_FIX.md להנחיות")
else:
    print("\n🎉 כל הבורסאות מחוברות! אתה מוכן לרוץ!")
