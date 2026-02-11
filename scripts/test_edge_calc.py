"""Quick test: RIVER edge with correct vs incorrect intervals."""
from decimal import Decimal
from src.discovery.calculator import calculate_funding_edge

# RIVER rates from live data
long_rate = Decimal("-0.007557")   # Binance (long)
short_rate = Decimal("-0.002060")  # Bybit (short)

# OLD logic: assumed both 8h → showed ~55 bps edge (WRONG)
old = calculate_funding_edge(long_rate, short_rate, 8, 8)
print(f"OLD (both 8h):       edge = {old['edge_bps']:.1f} bps")

# NEW logic: Binance=4h, Bybit=1h → negative edge (CORRECT)
new = calculate_funding_edge(long_rate, short_rate, 4, 1)
print(f"NEW (Bin=4h, Byb=1h): edge = {new['edge_bps']:.1f} bps")

# What if we flip: short Binance, long Bybit?
flipped = calculate_funding_edge(short_rate, long_rate, 1, 4)
print(f"FLIPPED (L=Byb 1h, S=Bin 4h): edge = {flipped['edge_bps']:.1f} bps")
