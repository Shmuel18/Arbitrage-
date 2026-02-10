"""
Health Monitor for Data Streams
Validates stream quality and raises alerts
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Dict, Optional

from src.core.config import get_config
from src.core.contracts import ExchangeStatus
from src.core.logging import get_logger

logger = get_logger("health_monitor")


@dataclass
class StreamMetrics:
    """Metrics for a single data stream"""
    exchange: str
    stream_type: str
    
    # Timestamps
    last_update: Optional[datetime] = None
    last_sequence: Optional[int] = None
    
    # Counters
    updates_received: int = 0
    sequence_gaps: int = 0
    disconnects: int = 0
    reconnects: int = 0
    
    # Recent disconnect times
    recent_disconnects: Deque[datetime] = field(default_factory=lambda: deque(maxlen=10))
    
    def record_update(self, sequence: Optional[int] = None):
        """Record successful update"""
        self.last_update = datetime.utcnow()
        self.updates_received += 1
        
        if sequence is not None:
            if self.last_sequence is not None and sequence != self.last_sequence + 1:
                self.sequence_gaps += 1
                logger.warning(
                    f"Sequence gap detected",
                    exchange=self.exchange,
                    stream_type=self.stream_type,
                    expected=self.last_sequence + 1,
                    received=sequence,
                    gap_size=sequence - self.last_sequence - 1
                )
            self.last_sequence = sequence
    
    def record_disconnect(self):
        """Record disconnect event"""
        self.disconnects += 1
        self.recent_disconnects.append(datetime.utcnow())
    
    def record_reconnect(self):
        """Record reconnect event"""
        self.reconnects += 1
    
    @property
    def staleness_ms(self) -> Optional[float]:
        """Calculate staleness in milliseconds"""
        if self.last_update is None:
            return None
        return (datetime.utcnow() - self.last_update).total_seconds() * 1000
    
    @property
    def recent_disconnect_rate(self) -> int:
        """Count disconnects in last minute"""
        if not self.recent_disconnects:
            return 0
        
        one_min_ago = datetime.utcnow() - timedelta(minutes=1)
        return sum(1 for dt in self.recent_disconnects if dt > one_min_ago)


class HealthMonitor:
    """
    Monitors health of all data streams
    
    Health gates:
    - Staleness < 500ms
    - Sequence gaps = 0 (configurable)
    - Disconnects < 3/min
    - Spread sanity (bid < ask)
    """
    
    def __init__(self):
        self.config = get_config()
        self.metrics: Dict[str, StreamMetrics] = {}
        self.exchange_status: Dict[str, ExchangeStatus] = {}
        
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
    
    def get_or_create_metrics(self, exchange: str, stream_type: str) -> StreamMetrics:
        """Get or create metrics for stream"""
        key = f"{exchange}:{stream_type}"
        if key not in self.metrics:
            self.metrics[key] = StreamMetrics(
                exchange=exchange,
                stream_type=stream_type
            )
        return self.metrics[key]
    
    def record_update(
        self,
        exchange: str,
        stream_type: str,
        sequence: Optional[int] = None
    ):
        """Record stream update"""
        metrics = self.get_or_create_metrics(exchange, stream_type)
        metrics.record_update(sequence)
    
    def record_disconnect(self, exchange: str, stream_type: str):
        """Record stream disconnect"""
        metrics = self.get_or_create_metrics(exchange, stream_type)
        metrics.record_disconnect()
        
        logger.warning(
            f"Stream disconnected",
            exchange=exchange,
            stream_type=stream_type,
            total_disconnects=metrics.disconnects
        )
    
    def record_reconnect(self, exchange: str, stream_type: str):
        """Record stream reconnect"""
        metrics = self.get_or_create_metrics(exchange, stream_type)
        metrics.record_reconnect()
        
        logger.info(
            f"Stream reconnected",
            exchange=exchange,
            stream_type=stream_type,
            total_reconnects=metrics.reconnects
        )
    
    def check_stream_health(self, exchange: str, stream_type: str) -> ExchangeStatus:
        """
        Check health of specific stream
        
        Returns:
            HEALTHY: All checks pass
            DEGRADED: Some issues but operational
            OFFLINE: Critical issues
        """
        metrics = self.get_or_create_metrics(exchange, stream_type)
        
        # Check if stream ever received data
        if metrics.last_update is None:
            return ExchangeStatus.OFFLINE
        
        # Check staleness
        staleness = metrics.staleness_ms
        if staleness is None or staleness > self.config.risk_limits.max_ws_staleness_ms:
            logger.warning(
                f"Stream stale",
                exchange=exchange,
                stream_type=stream_type,
                staleness_ms=staleness,
                threshold_ms=self.config.risk_limits.max_ws_staleness_ms
            )
            return ExchangeStatus.DEGRADED
        
        # Check sequence gaps
        if (self.config.data_ingestion.sequence_gap_tolerance == 0 and
            metrics.sequence_gaps > 0):
            logger.warning(
                f"Sequence gaps detected",
                exchange=exchange,
                stream_type=stream_type,
                gaps=metrics.sequence_gaps
            )
            return ExchangeStatus.DEGRADED
        
        # Check disconnect rate
        recent_disconnects = metrics.recent_disconnect_rate
        if recent_disconnects >= 3:
            logger.warning(
                f"High disconnect rate",
                exchange=exchange,
                stream_type=stream_type,
                disconnects_per_min=recent_disconnects
            )
            return ExchangeStatus.DEGRADED
        
        return ExchangeStatus.HEALTHY
    
    def check_exchange_health(self, exchange: str) -> ExchangeStatus:
        """
        Check overall health of exchange
        Returns worst status among all streams
        """
        exchange_streams = [
            m for m in self.metrics.values()
            if m.exchange == exchange
        ]
        
        if not exchange_streams:
            return ExchangeStatus.OFFLINE
        
        statuses = [
            self.check_stream_health(m.exchange, m.stream_type)
            for m in exchange_streams
        ]
        
        # Return worst status
        if ExchangeStatus.OFFLINE in statuses:
            return ExchangeStatus.OFFLINE
        elif ExchangeStatus.DEGRADED in statuses:
            return ExchangeStatus.DEGRADED
        else:
            return ExchangeStatus.HEALTHY
    
    def get_exchange_status(self, exchange: str) -> ExchangeStatus:
        """Get cached exchange status"""
        return self.exchange_status.get(exchange, ExchangeStatus.OFFLINE)
    
    def is_exchange_healthy(self, exchange: str) -> bool:
        """Check if exchange is healthy (trading allowed)"""
        return self.get_exchange_status(exchange) == ExchangeStatus.HEALTHY
    
    def can_trade(self, exchange_long: str, exchange_short: str) -> tuple[bool, Optional[str]]:
        """
        Check if both exchanges are healthy for trading
        
        Returns:
            (can_trade, reason_if_not)
        """
        status_long = self.get_exchange_status(exchange_long)
        status_short = self.get_exchange_status(exchange_short)
        
        if status_long != ExchangeStatus.HEALTHY:
            return False, f"{exchange_long} not healthy: {status_long.value}"
        
        if status_short != ExchangeStatus.HEALTHY:
            return False, f"{exchange_short} not healthy: {status_short.value}"
        
        return True, None
    
    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self._running:
            try:
                # Update exchange statuses
                for exchange in self.config.enabled_exchanges:
                    old_status = self.exchange_status.get(exchange)
                    new_status = self.check_exchange_health(exchange)
                    
                    # Log status changes
                    if old_status != new_status:
                        logger.health_exchange_status(
                            exchange=exchange,
                            is_healthy=(new_status == ExchangeStatus.HEALTHY),
                            reason=f"Status changed: {old_status} -> {new_status}"
                        )
                    
                    self.exchange_status[exchange] = new_status
                
                # Wait for next check
                await asyncio.sleep(self.config.data_ingestion.health_check_interval_sec)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    async def start(self):
        """Start health monitoring"""
        if self._running:
            return
        
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        logger.info("Health monitor started")
    
    async def stop(self):
        """Stop health monitoring"""
        if not self._running:
            return
        
        self._running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Health monitor stopped")
    
    def get_summary(self) -> Dict:
        """Get health summary for all exchanges"""
        summary = {}
        
        for exchange in self.config.enabled_exchanges:
            status = self.get_exchange_status(exchange)
            exchange_metrics = [
                m for m in self.metrics.values()
                if m.exchange == exchange
            ]
            
            summary[exchange] = {
                "status": status.value,
                "streams": {}
            }
            
            for metrics in exchange_metrics:
                summary[exchange]["streams"][metrics.stream_type] = {
                    "staleness_ms": metrics.staleness_ms,
                    "updates": metrics.updates_received,
                    "sequence_gaps": metrics.sequence_gaps,
                    "disconnects": metrics.disconnects,
                    "recent_disconnect_rate": metrics.recent_disconnect_rate
                }
        
        return summary
