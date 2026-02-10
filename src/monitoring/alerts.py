"""
Telegram Alert System
Real-time notifications for critical events
"""

import asyncio
from datetime import datetime
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from src.core.config import get_config
from src.core.contracts import SeverityLevel
from src.core.logging import get_logger

logger = get_logger("telegram_alerts")


class TelegramAlerter:
    """
    Telegram alert sender
    
    Alert levels:
    - 🔴 CRITICAL: Orphans, margin breach, liquidation risk
    - 🟡 WARNING: High slippage, WS issues, funding missed
    - 🟢 INFO: Trade opened/closed, daily summary
    """
    
    def __init__(self):
        self.config = get_config()
        self.bot: Optional[Bot] = None
        self.enabled = False
        
        if self.config.monitoring.enable_telegram:
            self._initialize_bot()
    
    def _initialize_bot(self):
        """Initialize Telegram bot"""
        try:
            if not self.config.monitoring.telegram_bot_token:
                logger.warning("Telegram bot token not configured")
                return
            
            if not self.config.monitoring.telegram_chat_id:
                logger.warning("Telegram chat ID not configured")
                return
            
            self.bot = Bot(token=self.config.monitoring.telegram_bot_token)
            self.enabled = True
            
            logger.info("Telegram alerter initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}", exc_info=True)
            self.enabled = False
    
    async def send_message(
        self,
        message: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ):
        """Send Telegram message"""
        if not self.enabled or not self.bot:
            logger.debug(f"Telegram disabled, would send: {message}")
            return
        
        try:
            await self.bot.send_message(
                chat_id=self.config.monitoring.telegram_chat_id,
                text=message,
                parse_mode=parse_mode,
                disable_notification=disable_notification
            )
            
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram: {e}", exc_info=True)
    
    # ==================== CRITICAL ALERTS ====================
    
    async def alert_orphan_detected(
        self,
        trade_id: str,
        symbol: str,
        exchange: str,
        quantity: float,
        orphan_time_ms: int
    ):
        """Alert on orphaned position"""
        message = (
            f"🔴 <b>ORPHAN DETECTED</b>\n\n"
            f"Trade: <code>{trade_id[:8]}...</code>\n"
            f"Symbol: {symbol}\n"
            f"Exchange: {exchange}\n"
            f"Quantity: {quantity}\n"
            f"Orphan Time: {orphan_time_ms}ms\n\n"
            f"⚠️ Immediate action required!"
        )
        await self.send_message(message)
        
        logger.critical(
            "Orphan alert sent",
            trade_id=trade_id,
            symbol=symbol,
            exchange=exchange
        )
    
    async def alert_margin_breach(
        self,
        current_usage_pct: float,
        threshold_pct: float,
        exchange: str
    ):
        """Alert on margin breach"""
        message = (
            f"🔴 <b>MARGIN BREACH</b>\n\n"
            f"Exchange: {exchange}\n"
            f"Current Usage: {current_usage_pct:.1f}%\n"
            f"Threshold: {threshold_pct:.1f}%\n\n"
            f"⚠️ Reducing positions..."
        )
        await self.send_message(message)
    
    async def alert_liquidation_risk(
        self,
        symbol: str,
        exchange: str,
        distance_pct: float,
        mark_price: float,
        liq_price: float
    ):
        """Alert on liquidation risk"""
        message = (
            f"🔴 <b>LIQUIDATION RISK</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Exchange: {exchange}\n"
            f"Distance: {distance_pct:.2f}%\n"
            f"Mark: ${mark_price:.2f}\n"
            f"Liq: ${liq_price:.2f}\n\n"
            f"⚠️ Emergency close initiated!"
        )
        await self.send_message(message)
    
    async def alert_system_error(self, error_type: str, message: str):
        """Alert on system error"""
        msg = (
            f"🔴 <b>SYSTEM ERROR</b>\n\n"
            f"Type: {error_type}\n"
            f"Message: {message}\n\n"
            f"⚠️ Check logs immediately!"
        )
        await self.send_message(msg)
    
    # ==================== WARNING ALERTS ====================
    
    async def alert_high_slippage(
        self,
        symbol: str,
        expected_bps: float,
        actual_bps: float,
        exchange: str
    ):
        """Alert on high slippage"""
        message = (
            f"🟡 <b>High Slippage</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Exchange: {exchange}\n"
            f"Expected: {expected_bps:.2f} bps\n"
            f"Actual: {actual_bps:.2f} bps\n"
        )
        await self.send_message(message, disable_notification=True)
    
    async def alert_ws_degraded(self, exchange: str, reason: str):
        """Alert on WebSocket issues"""
        message = (
            f"🟡 <b>WS Degraded</b>\n\n"
            f"Exchange: {exchange}\n"
            f"Reason: {reason}\n\n"
            f"Trading paused on this exchange"
        )
        await self.send_message(message, disable_notification=True)
    
    async def alert_funding_missed(
        self,
        symbol: str,
        expected_funding: float
    ):
        """Alert on missed funding"""
        message = (
            f"🟡 <b>Funding Missed</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Expected: {expected_funding:.4f}%\n\n"
            f"Position closed before funding"
        )
        await self.send_message(message, disable_notification=True)
    
    # ==================== INFO ALERTS ====================
    
    async def alert_trade_opened(
        self,
        trade_id: str,
        symbol: str,
        size_usd: float,
        expected_net_bps: float,
        exchange_long: str,
        exchange_short: str
    ):
        """Alert on trade opened"""
        message = (
            f"🟢 <b>Trade Opened</b>\n\n"
            f"ID: <code>{trade_id[:8]}...</code>\n"
            f"Symbol: {symbol}\n"
            f"Size: ${size_usd:,.2f}\n"
            f"Expected: {expected_net_bps:.2f} bps\n"
            f"Long: {exchange_long}\n"
            f"Short: {exchange_short}"
        )
        await self.send_message(message, disable_notification=True)
    
    async def alert_trade_closed(
        self,
        trade_id: str,
        symbol: str,
        realized_pnl: float,
        expected_bps: float,
        actual_bps: float,
        duration_hours: float
    ):
        """Alert on trade closed"""
        emoji = "💰" if realized_pnl > 0 else "📉"
        
        message = (
            f"{emoji} <b>Trade Closed</b>\n\n"
            f"ID: <code>{trade_id[:8]}...</code>\n"
            f"Symbol: {symbol}\n"
            f"P&L: ${realized_pnl:,.2f}\n"
            f"Expected: {expected_bps:.2f} bps\n"
            f"Actual: {actual_bps:.2f} bps\n"
            f"Duration: {duration_hours:.1f}h"
        )
        await self.send_message(message, disable_notification=True)
    
    async def send_daily_summary(
        self,
        date: str,
        total_trades: int,
        profitable_trades: int,
        total_pnl: float,
        total_fees: float,
        avg_holding_hours: float
    ):
        """Send daily summary"""
        win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0
        
        message = (
            f"📊 <b>Daily Summary - {date}</b>\n\n"
            f"Trades: {total_trades}\n"
            f"Profitable: {profitable_trades} ({win_rate:.1f}%)\n"
            f"Total P&L: ${total_pnl:,.2f}\n"
            f"Total Fees: ${total_fees:,.2f}\n"
            f"Avg Hold: {avg_holding_hours:.1f}h\n\n"
            f"{'💰 Profitable day!' if total_pnl > 0 else '📉 Loss day'}"
        )
        await self.send_message(message)
    
    async def send_startup_notification(self):
        """Send startup notification"""
        mode = "LIVE" if not self.config.paper_trading else "PAPER"
        emoji = "🔴" if not self.config.paper_trading else "📝"
        
        message = (
            f"{emoji} <b>Trinity Engine Started</b>\n\n"
            f"Mode: {mode}\n"
            f"Environment: {self.config.environment}\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
            f"System operational ✅"
        )
        await self.send_message(message)
    
    async def send_shutdown_notification(self):
        """Send shutdown notification"""
        message = (
            f"⏹️ <b>Trinity Engine Stopped</b>\n\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
            f"All positions should be closed"
        )
        await self.send_message(message)


# Singleton instance
_telegram_instance: Optional[TelegramAlerter] = None


def get_telegram_alerter() -> TelegramAlerter:
    """Get Telegram alerter instance"""
    global _telegram_instance
    
    if _telegram_instance is None:
        _telegram_instance = TelegramAlerter()
    
    return _telegram_instance
