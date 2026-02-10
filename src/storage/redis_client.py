"""
Redis State Management
Distributed state store with TTL and locking
"""

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

import redis.asyncio as aioredis

from src.core.config import get_config
from src.core.logging import get_logger

logger = get_logger("redis_client")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder for Decimal types"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class RedisClient:
    """
    Redis client for state management
    
    Key patterns:
    - trade:{id}:state
    - trade:{id}:legs:{ex}
    - exchange:{ex}:health
    - symbol:{sym}:cooldown_until
    - locks:capital:{sym}
    - metrics:latency:{ex}
    """
    
    def __init__(self):
        self.config = get_config()
        self.redis: Optional[aioredis.Redis] = None
        self._key_prefix = self.config.redis.key_prefix
    
    async def connect(self):
        """Connect to Redis"""
        try:
            self.redis = await aioredis.from_url(
                self.config.redis.url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20
            )
            
            # Test connection
            await self.redis.ping()
            
            logger.info("Connected to Redis")
            
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
            # Fallback to fakeredis for paper trading
            if self.config.paper_trading or self.config.dry_run:
                try:
                    import fakeredis.aioredis as fakeredis_aio
                    self.redis = fakeredis_aio.FakeRedis(
                        encoding="utf-8",
                        decode_responses=True
                    )
                    logger.info("Using in-memory FakeRedis (paper trading mode)")
                except ImportError:
                    logger.error("fakeredis not installed. Run: pip install fakeredis")
                    raise
            else:
                raise
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis:
            await self.redis.close()
            logger.info("Disconnected from Redis")
    
    def _make_key(self, *parts: str) -> str:
        """Create prefixed key"""
        return self._key_prefix + ":".join(parts)
    
    # ==================== TRADE STATE ====================
    
    async def set_trade_state(
        self,
        trade_id: UUID,
        state_data: Dict[str, Any],
        ttl_sec: Optional[int] = None
    ):
        """Set trade state"""
        key = self._make_key("trade", str(trade_id), "state")
        value = json.dumps(state_data, cls=DecimalEncoder)
        
        if ttl_sec:
            await self.redis.setex(key, ttl_sec, value)
        else:
            await self.redis.set(key, value)
    
    async def get_trade_state(self, trade_id: UUID) -> Optional[Dict[str, Any]]:
        """Get trade state"""
        key = self._make_key("trade", str(trade_id), "state")
        value = await self.redis.get(key)
        
        if value:
            return json.loads(value)
        return None
    
    async def delete_trade_state(self, trade_id: UUID):
        """Delete trade state"""
        key = self._make_key("trade", str(trade_id), "state")
        await self.redis.delete(key)
    
    # ==================== EXCHANGE HEALTH ====================
    
    async def set_exchange_health(
        self,
        exchange: str,
        status: str,
        ttl_sec: int = 60
    ):
        """Set exchange health status"""
        key = self._make_key("exchange", exchange, "health")
        await self.redis.setex(key, ttl_sec, status)
    
    async def get_exchange_health(self, exchange: str) -> Optional[str]:
        """Get exchange health status"""
        key = self._make_key("exchange", exchange, "health")
        return await self.redis.get(key)

    # ==================== POSITION SNAPSHOTS ====================

    async def set_position_snapshot(
        self,
        exchange: str,
        symbol: str,
        snapshot: Dict[str, Any],
        ttl_sec: int = 300
    ):
        """Store latest position snapshot"""
        key = self._make_key("position", exchange, symbol)
        value = json.dumps(snapshot, cls=DecimalEncoder)
        await self.redis.setex(key, ttl_sec, value)

    async def get_position_snapshot(self, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest position snapshot"""
        key = self._make_key("position", exchange, symbol)
        value = await self.redis.get(key)
        if value:
            return json.loads(value)
        return None
    
    # ==================== COOLDOWNS ====================
    
    async def set_cooldown(
        self,
        symbol: str,
        cooldown_until: datetime
    ):
        """Set symbol cooldown"""
        key = self._make_key("symbol", symbol, "cooldown_until")
        ttl_sec = int((cooldown_until - datetime.utcnow()).total_seconds())
        
        if ttl_sec > 0:
            await self.redis.setex(key, ttl_sec, cooldown_until.isoformat())
    
    async def get_cooldown(self, symbol: str) -> Optional[datetime]:
        """Get symbol cooldown"""
        key = self._make_key("symbol", symbol, "cooldown_until")
        value = await self.redis.get(key)
        
        if value:
            return datetime.fromisoformat(value)
        return None
    
    async def is_cooled_down(self, symbol: str) -> bool:
        """Check if symbol is in cooldown"""
        cooldown = await self.get_cooldown(symbol)
        if cooldown:
            return datetime.utcnow() < cooldown
        return False
    
    # ==================== DISTRIBUTED LOCKS ====================
    
    async def acquire_lock(
        self,
        lock_name: str,
        timeout_sec: Optional[int] = None
    ) -> bool:
        """
        Acquire distributed lock
        Returns True if acquired, False if already locked
        """
        key = self._make_key("locks", lock_name)
        timeout = timeout_sec or self.config.redis.lock_timeout_sec
        
        # Try to set with NX (only if not exists)
        result = await self.redis.set(
            key,
            "locked",
            nx=True,
            ex=timeout
        )
        
        return bool(result)
    
    async def release_lock(self, lock_name: str):
        """Release distributed lock"""
        key = self._make_key("locks", lock_name)
        await self.redis.delete(key)
    
    async def extend_lock(self, lock_name: str, additional_sec: int):
        """Extend lock TTL"""
        key = self._make_key("locks", lock_name)
        await self.redis.expire(key, additional_sec)
    
    # ==================== METRICS ====================
    
    async def record_latency(
        self,
        exchange: str,
        operation: str,
        latency_ms: float
    ):
        """Record operation latency"""
        key = self._make_key("metrics", "latency", exchange, operation)
        
        # Use sorted set with timestamp as score
        timestamp = datetime.utcnow().timestamp()
        await self.redis.zadd(key, {str(latency_ms): timestamp})
        
        # Keep only last 1000 entries
        await self.redis.zremrangebyrank(key, 0, -1001)
        
        # Set TTL
        await self.redis.expire(key, 3600)
    
    async def get_avg_latency(
        self,
        exchange: str,
        operation: str,
        window_sec: int = 60
    ) -> Optional[float]:
        """Get average latency for operation"""
        key = self._make_key("metrics", "latency", exchange, operation)
        
        # Get recent values
        cutoff = datetime.utcnow().timestamp() - window_sec
        values = await self.redis.zrangebyscore(key, cutoff, "+inf")
        
        if values:
            latencies = [float(v) for v in values]
            return sum(latencies) / len(latencies)
        return None
    
    # ==================== CACHE ====================
    
    async def cache_set(
        self,
        cache_key: str,
        value: Any,
        ttl_sec: Optional[int] = None
    ):
        """Set cache value"""
        key = self._make_key("cache", cache_key)
        value_str = json.dumps(value, cls=DecimalEncoder)
        
        if ttl_sec:
            await self.redis.setex(key, ttl_sec, value_str)
        else:
            await self.redis.set(key, value_str)
    
    async def cache_get(self, cache_key: str) -> Optional[Any]:
        """Get cache value"""
        key = self._make_key("cache", cache_key)
        value = await self.redis.get(key)
        
        if value:
            return json.loads(value)
        return None
    
    async def cache_delete(self, cache_key: str):
        """Delete cache value"""
        key = self._make_key("cache", cache_key)
        await self.redis.delete(key)
    
    # ==================== UTILITIES ====================
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment counter"""
        full_key = self._make_key(key)
        return await self.redis.incrby(full_key, amount)
    
    async def get_counter(self, key: str) -> int:
        """Get counter value"""
        full_key = self._make_key(key)
        value = await self.redis.get(full_key)
        return int(value) if value else 0
    
    async def flush_pattern(self, pattern: str):
        """Delete all keys matching pattern"""
        full_pattern = self._make_key(pattern)
        
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor,
                match=full_pattern,
                count=100
            )
            
            if keys:
                await self.redis.delete(*keys)
            
            if cursor == 0:
                break
    
    async def health_check(self) -> bool:
        """Check Redis health"""
        try:
            return await self.redis.ping()
        except Exception:
            return False


# Singleton instance
_redis_instance: Optional[RedisClient] = None


async def get_redis() -> RedisClient:
    """Get Redis client instance"""
    global _redis_instance
    
    if _redis_instance is None:
        _redis_instance = RedisClient()
        await _redis_instance.connect()
    
    return _redis_instance
