#!/usr/bin/env python3
"""Show account balances and positions from all exchanges"""
import asyncio
import os
from dotenv import load_dotenv
import ccxt.pro as ccxtpro

load_dotenv()

async def check_exchange(name: str, ccxt_id: str, default_type: str):
    """Check balance and positions for one exchange"""
    print(f"\n{'='*60}")
    print(f"🔍 {name.upper()} ({ccxt_id})")
    print(f"{'='*60}")
    
    api_key = os.getenv(f"{name.upper()}_API_KEY")
    api_secret = os.getenv(f"{name.upper()}_API_SECRET")
    
    if not api_key or not api_secret:
        print(f"❌ No API credentials found for {name}")
        return
    
    exchange = ccxtpro.__dict__[ccxt_id]({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {'defaultType': default_type},
    })
    
    try:
        # 1. Balance
        print("\n💰 BALANCE:")
        balance = await exchange.fetch_balance()
        
        # Show all non-zero balances
        for currency, amount in balance['total'].items():
            if amount and float(amount) > 0:
                free = balance['free'].get(currency, 0)
                used = balance['used'].get(currency, 0)
                print(f"   {currency:8} | Total: {amount:12.4f} | Free: {free:12.4f} | Used: {used:12.4f}")
        
        # 2. Positions
        print("\n📊 OPEN POSITIONS:")
        try:
            positions = await exchange.fetch_positions()
            open_positions = [p for p in positions if p.get('contracts', 0) and float(p.get('contracts', 0)) != 0]
            
            if not open_positions:
                print("   ✅ No open positions")
            else:
                for pos in open_positions:
                    symbol = pos['symbol']
                    side = pos['side']
                    contracts = pos.get('contracts', 0)
                    notional = pos.get('notional', 0)
                    unrealized_pnl = pos.get('unrealizedPnl', 0)
                    leverage = pos.get('leverage', 1)
                    
                    print(f"   {symbol:20} | {side:5} | Qty: {contracts:10.4f} | Notional: ${notional:10.2f} | PnL: ${unrealized_pnl:8.2f} | Lev: {leverage}x")
        except Exception as e:
            print(f"   ⚠️  Could not fetch positions: {e}")
        
        # 3. Account info
        print("\n📋 ACCOUNT INFO:")
        try:
            if ccxt_id == 'binanceusdm':
                account = await exchange.fapiPrivateV2GetAccount()
                total_wallet = float(account.get('totalWalletBalance', 0))
                total_unrealized = float(account.get('totalUnrealizedProfit', 0))
                total_margin = float(account.get('totalInitialMargin', 0))
                available = float(account.get('availableBalance', 0))
                
                print(f"   Total Wallet Balance: ${total_wallet:.2f}")
                print(f"   Unrealized PnL:       ${total_unrealized:.2f}")
                print(f"   Used Margin:          ${total_margin:.2f}")
                print(f"   Available Balance:    ${available:.2f}")
                
            elif ccxt_id == 'bybit':
                account = await exchange.fetch_balance()
                usdt_info = account.get('USDT', {})
                print(f"   Total Balance:  ${usdt_info.get('total', 0):.2f}")
                print(f"   Free Balance:   ${usdt_info.get('free', 0):.2f}")
                print(f"   Used (Margin):  ${usdt_info.get('used', 0):.2f}")
        except Exception as e:
            print(f"   ⚠️  Could not fetch account info: {e}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await exchange.close()

async def main():
    print("🤖 Trinity Arbitrage - Account Information")
    print("=" * 60)
    
    exchanges = [
        ("binance", "binanceusdm", "future"),
        ("bybit", "bybit", "swap"),
        ("gateio", "gateio", "swap"),
        ("okx", "okx", "swap"),
    ]
    
    for name, ccxt_id, default_type in exchanges:
        await check_exchange(name, ccxt_id, default_type)
    
    print(f"\n{'='*60}")
    print("✅ Done!")

if __name__ == '__main__':
    asyncio.run(main())
