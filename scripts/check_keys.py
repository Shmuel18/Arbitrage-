"""Check which exchange API keys are configured in .env"""
from dotenv import load_dotenv
import os

load_dotenv()

keys = {
    "Binance": ["BINANCE_API_KEY", "BINANCE_API_SECRET"],
    "Bybit": ["BYBIT_API_KEY", "BYBIT_API_SECRET"],
    "OKX": ["OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE"],
    "GateIO": ["GATEIO_API_KEY", "GATEIO_API_SECRET"],
    "KuCoin": ["KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_PASSPHRASE"],
}

for exchange, env_vars in keys.items():
    statuses = []
    for var in env_vars:
        val = os.getenv(var)
        if val:
            statuses.append(f"{var}=SET")
        else:
            statuses.append(f"{var}=MISSING")
    all_set = all(os.getenv(v) for v in env_vars)
    icon = "✅" if all_set else "❌"
    print(f"{icon} {exchange}: {', '.join(statuses)}")
