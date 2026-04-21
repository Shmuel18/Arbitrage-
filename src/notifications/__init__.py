"""Notifications package — Telegram bot integration.

Modules:
    telegram_notifier  — thin aiohttp wrapper around Bot API sendMessage.
    daily_summary      — asyncio task scheduling the nightly digest.
    bot_commands       — /start /status /menu handler loop.

The Telegram path is strictly additive: the bot's existing Redis + WebSocket
alert pipeline (see src/api/publisher.py::publish_alert) remains the source
of truth. Telegram is a *fan-out* sink that subscribes to the same events
the AlertBell UI consumes.
"""

from __future__ import annotations

from src.notifications.telegram_notifier import TelegramNotifier, format_alert

__all__ = ["TelegramNotifier", "format_alert"]
