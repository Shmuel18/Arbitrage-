"""
Structured Logging System
Production-grade logging with audit trail
"""

import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from src.core.config import get_config


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        
        # Base structure
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        # Add standard fields
        if hasattr(record, "trade_id"):
            log_data["trade_id"] = str(record.trade_id)
        if hasattr(record, "exchange"):
            log_data["exchange"] = record.exchange
        if hasattr(record, "symbol"):
            log_data["symbol"] = record.symbol
        if hasattr(record, "component"):
            log_data["component"] = record.component
        
        return json.dumps(log_data, default=str)


class TextFormatter(logging.Formatter):
    """
    Human-readable text formatter
    """
    
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


class TrinityLogger:
    """
    Central logging manager for Trinity system
    """
    
    def __init__(self, name: str = "trinity"):
        self.name = name
        self.config = get_config()
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        """Setup logger with handlers"""
        logger = logging.getLogger(self.name)
        logger.setLevel(self.config.logging.level)
        logger.propagate = False
        
        # Remove existing handlers
        logger.handlers.clear()
        
        # Console handler
        if self.config.logging.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.config.logging.level)
            
            if self.config.logging.format == "json":
                console_handler.setFormatter(StructuredFormatter())
            else:
                console_handler.setFormatter(TextFormatter())
            
            logger.addHandler(console_handler)
        
        # File handler
        if self.config.logging.file_output:
            log_dir = Path(self.config.logging.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            
            log_file = log_dir / f"trinity_{datetime.utcnow().strftime('%Y%m%d')}.log"
            
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=self.config.logging.max_file_size_mb * 1024 * 1024,
                backupCount=self.config.logging.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(self.config.logging.level)
            file_handler.setFormatter(StructuredFormatter())
            
            logger.addHandler(file_handler)
        
        return logger
    
    def _add_context(self, extra: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Add context fields to log record"""
        if extra is None:
            extra = {}
        
        extra_fields = extra.get("extra_fields", {})
        
        # Add kwargs to extra fields
        for key, value in kwargs.items():
            # Convert UUIDs to strings
            if isinstance(value, UUID):
                value = str(value)
            extra_fields[key] = value
        
        extra["extra_fields"] = extra_fields
        return extra
    
    def debug(self, message: str, **kwargs):
        """Debug level log"""
        extra = self._add_context({}, **kwargs)
        self.logger.debug(message, extra=extra)
    
    def info(self, message: str, **kwargs):
        """Info level log"""
        extra = self._add_context({}, **kwargs)
        self.logger.info(message, extra=extra)
    
    def warning(self, message: str, **kwargs):
        """Warning level log"""
        extra = self._add_context({}, **kwargs)
        self.logger.warning(message, extra=extra)
    
    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Error level log"""
        extra = self._add_context({}, **kwargs)
        self.logger.error(message, exc_info=exc_info, extra=extra)
    
    def critical(self, message: str, exc_info: bool = False, **kwargs):
        """Critical level log"""
        extra = self._add_context({}, **kwargs)
        self.logger.critical(message, exc_info=exc_info, extra=extra)
    
    # ==================== AUDIT LOGS ====================
    
    def audit_opportunity(
        self,
        opportunity_id: UUID,
        symbol: str,
        exchanges: tuple[str, str],
        expected_net_bps: float,
        size_usd: float,
        executed: bool,
        reject_reason: Optional[str] = None
    ):
        """Audit opportunity discovery"""
        self.info(
            f"AUDIT: Opportunity {'EXECUTED' if executed else 'REJECTED'}",
            component="discovery",
            opportunity_id=opportunity_id,
            symbol=symbol,
            exchange_long=exchanges[0],
            exchange_short=exchanges[1],
            expected_net_bps=expected_net_bps,
            size_usd=size_usd,
            executed=executed,
            reject_reason=reject_reason
        )
    
    def audit_order(
        self,
        order_id: UUID,
        trade_id: Optional[UUID],
        exchange: str,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float],
        status: str,
        exchange_order_id: Optional[str] = None
    ):
        """Audit order submission/fill"""
        self.info(
            f"AUDIT: Order {status}",
            component="execution",
            order_id=order_id,
            trade_id=trade_id,
            exchange=exchange,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=status,
            exchange_order_id=exchange_order_id
        )
    
    def audit_trade_state(
        self,
        trade_id: UUID,
        old_state: str,
        new_state: str,
        reason: Optional[str] = None
    ):
        """Audit trade state transitions"""
        self.info(
            f"AUDIT: Trade state change {old_state} -> {new_state}",
            component="state_machine",
            trade_id=trade_id,
            old_state=old_state,
            new_state=new_state,
            reason=reason
        )
    
    def audit_risk_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        measured_value: Optional[float] = None,
        threshold_value: Optional[float] = None,
        action_taken: Optional[str] = None,
        **kwargs
    ):
        """Audit risk events"""
        self.warning(
            f"AUDIT: Risk event - {message}",
            component="risk_guard",
            event_type=event_type,
            severity=severity,
            measured_value=measured_value,
            threshold_value=threshold_value,
            action_taken=action_taken,
            **kwargs
        )
    
    def audit_reconciliation(
        self,
        exchange: str,
        symbol: str,
        expected_quantity: float,
        actual_quantity: float,
        mismatch: float,
        action_taken: Optional[str] = None
    ):
        """Audit position reconciliation"""
        level = self.error if abs(mismatch) > 0.0001 else self.info
        level(
            f"AUDIT: Reconciliation {'MISMATCH' if abs(mismatch) > 0.0001 else 'OK'}",
            component="reconciliation",
            exchange=exchange,
            symbol=symbol,
            expected_quantity=expected_quantity,
            actual_quantity=actual_quantity,
            mismatch=mismatch,
            action_taken=action_taken
        )
    
    def audit_pnl(
        self,
        trade_id: UUID,
        symbol: str,
        expected_net_bps: float,
        realized_pnl_usd: float,
        total_fees_usd: float,
        entry_time: datetime,
        exit_time: datetime
    ):
        """Audit trade P&L"""
        duration_sec = (exit_time - entry_time).total_seconds()
        
        self.info(
            "AUDIT: Trade P&L",
            component="execution",
            trade_id=trade_id,
            symbol=symbol,
            expected_net_bps=expected_net_bps,
            realized_pnl_usd=realized_pnl_usd,
            total_fees_usd=total_fees_usd,
            duration_sec=duration_sec,
            entry_time=entry_time.isoformat(),
            exit_time=exit_time.isoformat()
        )
    
    # ==================== HEALTH LOGS ====================
    
    def health_ws_status(
        self,
        exchange: str,
        status: str,
        staleness_ms: Optional[float] = None,
        disconnects: int = 0
    ):
        """Log WebSocket health status"""
        self.debug(
            f"Health: WS {status}",
            component="data_ingestion",
            exchange=exchange,
            status=status,
            staleness_ms=staleness_ms,
            disconnects=disconnects
        )
    
    def health_exchange_status(
        self,
        exchange: str,
        is_healthy: bool,
        reason: Optional[str] = None
    ):
        """Log exchange health status"""
        level = self.info if is_healthy else self.warning
        level(
            f"Health: Exchange {'HEALTHY' if is_healthy else 'DEGRADED'}",
            component="health_monitor",
            exchange=exchange,
            is_healthy=is_healthy,
            reason=reason
        )
    
    # ==================== PERFORMANCE LOGS ====================
    
    def perf_latency(
        self,
        operation: str,
        latency_ms: float,
        exchange: Optional[str] = None,
        **kwargs
    ):
        """Log operation latency"""
        self.debug(
            f"Performance: {operation} took {latency_ms:.2f}ms",
            component="performance",
            operation=operation,
            latency_ms=latency_ms,
            exchange=exchange,
            **kwargs
        )
    
    def perf_orderbook_depth(
        self,
        exchange: str,
        symbol: str,
        bid_depth_usd: float,
        ask_depth_usd: float
    ):
        """Log orderbook depth metrics"""
        self.debug(
            "Performance: Orderbook depth",
            component="data_ingestion",
            exchange=exchange,
            symbol=symbol,
            bid_depth_usd=bid_depth_usd,
            ask_depth_usd=ask_depth_usd
        )


# Global logger instances
_loggers: Dict[str, TrinityLogger] = {}


def get_logger(name: str = "trinity") -> TrinityLogger:
    """
    Get or create logger instance
    """
    if name not in _loggers:
        _loggers[name] = TrinityLogger(name)
    return _loggers[name]


def init_logging():
    """
    Initialize logging system
    Call this at application startup
    """
    logger = get_logger()
    logger.info("=" * 80)
    logger.info("Trinity Arbitrage Engine V2.1-FINAL")
    logger.info("Logging system initialized")
    logger.info("=" * 80)
    return logger
