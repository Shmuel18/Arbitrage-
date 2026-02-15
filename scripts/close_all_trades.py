#!/usr/bin/env python3
"""
Script to close all active trades immediately.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import init_config
from src.exchanges.adapter import ExchangeManager
from src.execution.controller import ExecutionController
from src.risk.guard import RiskGuard
from src.storage.redis_client import RedisClient
from src.core.logging import get_logger

logger = get_logger("close_all")

async def main():
    """Close all active trades."""
    print("🚨 CLOSING ALL TRADES...")
    
    # Initialize config
    cfg = init_config()
    
    # Connect to Redis
    redis = RedisClient(
        url=cfg.redis.url,
        prefix=cfg.redis.key_prefix,
    )
    await redis.connect()
    print("✅ Connected to Redis")
    
    # Initialize exchange manager
    mgr = ExchangeManager()
    for eid in cfg.enabled_exchanges:
        exc_cfg = cfg.exchanges.get(eid)
        if not exc_cfg:
            continue
        mgr.register(eid, exc_cfg.model_dump())
    
    await mgr.connect_all()
    verified = await mgr.verify_all()
    print(f"✅ Connected to {len(verified)} exchanges: {verified}")
    
    # Initialize controller
    guard = RiskGuard(cfg, mgr, redis)
    controller = ExecutionController(cfg, mgr, redis, guard)
    await controller.start()
    
    # Close all positions
    print("🔴 Closing all positions...")
    await controller.close_all_positions()
    
    # Cleanup
    await controller.stop()
    await guard.stop()
    await mgr.disconnect_all()
    await redis.disconnect()
    
    print("✅ All trades closed successfully!")

if __name__ == "__main__":
    asyncio.run(main())