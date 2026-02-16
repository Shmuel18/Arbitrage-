"""
Trinity Bot - API Publisher
Publishes bot data to Redis for API consumption
"""

import json
from datetime import datetime
from typing import Dict, List, Any
import asyncio


class APIPublisher:
    """Publishes bot data to Redis for web interface"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.start_time = datetime.utcnow()
        self._total_trades = 0
        self._winning_trades = 0
    
    async def publish_status(self, running: bool, exchanges: List[str], positions_count: int):
        """Publish bot status"""
        status = {
            "bot_running": running,
            "connected_exchanges": exchanges,
            "active_positions": positions_count,
            "uptime": round((datetime.utcnow() - self.start_time).total_seconds() / 3600, 2)
        }
        await self.redis.set("trinity:status", json.dumps(status), ex=15)
    
    async def publish_balances(self, balances: Dict[str, float]):
        """Publish exchange balances"""
        data = {
            "balances": balances,
            "total": sum(balances.values()),
            "updated_at": datetime.utcnow().isoformat()
        }
        await self.redis.set("trinity:balances", json.dumps(data))
    
    async def publish_opportunities(self, opportunities: List[Dict[str, Any]]):
        """Publish top opportunities from scanner"""
        data = {
            "opportunities": opportunities,
            "count": len(opportunities),
            "updated_at": datetime.utcnow().isoformat()
        }
        await self.redis.set("trinity:opportunities", json.dumps(data))
    
    async def publish_log(self, level: str, message: str):
        """Publish a log entry"""
        entry = json.dumps({
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "message": message,
            "level": level
        })
        # Push to a list, keep last 200
        await self.redis._client.lpush("trinity:logs", entry)
        await self.redis._client.ltrim("trinity:logs", 0, 199)
    
    async def publish_summary(self, balances: Dict[str, float], positions_count: int):
        """Publish overall summary"""
        total_balance = sum(balances.values())
        win_rate = (self._winning_trades / self._total_trades) if self._total_trades > 0 else 0
        uptime = round((datetime.utcnow() - self.start_time).total_seconds() / 3600, 2)
        
        summary = {
            "total_pnl": total_balance,
            "total_trades": self._total_trades,
            "win_rate": round(win_rate, 3),
            "active_positions": positions_count,
            "uptime_hours": uptime
        }
        await self.redis.set("trinity:summary", json.dumps(summary))
    
    def record_trade(self, is_win: bool):
        """Record a trade result for win rate tracking"""
        self._total_trades += 1
        if is_win:
            self._winning_trades += 1
    
    async def publish_positions(self, positions: List[Dict[str, Any]]):
        """Publish active positions"""
        await self.redis.set("trinity:positions", json.dumps(positions))
    
    async def publish_trade(self, trade: Dict[str, Any]):
        """Publish completed trade to history"""
        timestamp = datetime.utcnow().timestamp()
        await self.redis.zadd(
            "trinity:trades:history",
            {json.dumps(trade): timestamp}
        )
    
    async def publish_exchanges(self, exchanges: List[Dict[str, Any]]):
        """Publish exchange statuses"""
        await self.redis.set("trinity:exchanges", json.dumps({"exchanges": exchanges}))

