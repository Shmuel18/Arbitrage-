#!/usr/bin/env python3
"""Clear all Trinity data from Redis"""
import asyncio
import os
from dotenv import load_dotenv
import redis.asyncio as aioredis

load_dotenv()

async def clear_all():
    r = await aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    
    print(">>> Searching for Trinity keys...")
    keys = await r.keys("trinity:*")
    
    if not keys:
        print("[+] No keys found - Redis is clean")
        await r.aclose()
        return
    
    print(f"[i] Found {len(keys)} keys:")
    for key in keys[:10]:  # Show first 10
        print(f"   - {key.decode()}")
    if len(keys) > 10:
        print(f"   ... and {len(keys) - 10} more")
    
    answer = input("\n[!] Delete all Trinity keys? (y/n): ")
    if answer.lower() not in ["y", "yes"]:
        print("[x] Aborted")
        await r.aclose()
        return
    
    deleted = await r.delete(*keys)
    print(f"\n[+] Deleted {deleted} keys from Redis")
    await r.aclose()

if __name__ == '__main__':
    asyncio.run(clear_all())
