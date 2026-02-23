"""
Debug script - Check if .env is loading correctly
Run from project root: python scripts/check_env.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

print("=" * 60)
print("Environment Debug Check")
print("=" * 60)

# 1. Check working directory
cwd = Path.cwd()
print(f"\n1. Current working directory:\n   {cwd}")

# 2. Check if .env exists
env_file = cwd / ".env"
print(f"\n2. Looking for .env at:\n   {env_file}")
print(f"   Exists: {env_file.exists()}")

if env_file.exists():
    print(f"   Size: {env_file.stat().st_size} bytes")
    print(f"   Readable: {os.access(env_file, os.R_OK)}")

# 3. Try to find .env automatically
found_env = find_dotenv()
print(f"\n3. dotenv find_dotenv() result:\n   {found_env if found_env else 'NOT FOUND'}")

# 4. Load .env
print(f"\n4. Loading .env...")
load_result = load_dotenv()
print(f"   load_dotenv() returned: {load_result}")

# 5. Check Bitget credentials
print(f"\n5. Checking Bitget environment variables:")
bitget_vars = {
    "BITGET_API_KEY": os.getenv("BITGET_API_KEY"),
    "BITGET_API_SECRET": os.getenv("BITGET_API_SECRET"),
    "BITGET_PASSPHRASE": os.getenv("BITGET_PASSPHRASE"),
    "BITGET_TESTNET": os.getenv("BITGET_TESTNET"),
}

for key, value in bitget_vars.items():
    if value:
        masked = f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "***"
        print(f"   ✓ {key}: {masked} (length: {len(value)})")
    else:
        print(f"   ✗ {key}: NOT SET")

# 6. Check all exchange credentials
print(f"\n6. All exchange API keys status:")
exchanges = ["BINANCE", "BYBIT", "OKX", "KUCOIN", "GATEIO", "BITGET"]
for exchange in exchanges:
    api_key = os.getenv(f"{exchange}_API_KEY")
    exists = "✓" if api_key else "✗"
    print(f"   {exists} {exchange}_API_KEY")

print("\n" + "=" * 60)
print("TROUBLESHOOTING:")
print("- If .env NOT FOUND: Make sure you're running from project root")
print("- If .env exists but vars NOT SET: Check file encoding (should be UTF-8)")
print("- If vars show as empty: Check for extra spaces around = signs")
print("=" * 60)
