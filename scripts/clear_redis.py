#!/usr/bin/env python3
"""Clear all Trinity data from Redis"""
import asyncio
import os
from dotenv import load_dotenv
import redis.asyncio as aioredis

load_dotenv()

async def clear_all():
    r = await aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    
    print("🔍 Searching for Trinity keys...")
    keys = await r.keys("trinity:*")
    
    if not keys:
        print("✅ No keys found - Redis is clean")
        await r.close()
        return
    
    print(f"📋 Found {len(keys)} keys:")
    for key in keys[:10]:  # Show first 10
        print(f"   - {key.decode()}")
    if len(keys) > 10:
        print(f"   ... and {len(keys) - 10} more")
    
    answer = input("\n⚠️  Delete all Trinity keys? (yes/no): ")
    if answer.lower() != "yes":
        print("❌ Aborted")
        await r.close()
        return
    
    deleted = await r.delete(*keys)
    print(f"\n✅ Deleted {deleted} keys from Redis")
    await r.close()

if __name__ == '__main__':
    asyncio.run(clear_all())
