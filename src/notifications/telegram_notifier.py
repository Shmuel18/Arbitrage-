"""Telegram Bot API notifier.

A minimal aiohttp wrapper around `POST /bot<token>/sendMessage`. Kept
deliberately dependency-light: no `python-telegram-bot`, no webhook setup,
no polling here (bot_commands.py handles that separately).

Design contract
---------------
* **Fire-and-forget.** `send()` never raises. A dead Telegram API must
  never crash the trading engine — the whole class wraps its body in
  broad except and logs errors at DEBUG/WARNING.
* **Fan-out, not replacement.** Called from APIPublisher.publish_alert
  after the Redis write; the Redis/WebSocket path continues to drive the
  in-app AlertBell regardless of Telegram availability.
* **Secrets boundary.** The bot token is held as SecretStr in config;
  we only unwrap it inside `_send()` at the HTTP call.
* **Quiet hours.** `silent_from_hour..silent_until_hour` sends with
  `disable_notification=true` — the message still arrives but doesn't
  ring/vibrate the device.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import html
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp

if TYPE_CHECKING:
    from src.core.config import TelegramConfig

logger = logging.getLogger("trinity.telegram")

_TELEGRAM_API_BASE = "https://api.telegram.org"
_HTTP_TIMEOUT_SECS = 10
_MAX_MESSAGE_CHARS = 4096  # Telegram hard limit


# ── Formatter ────────────────────────────────────────────────────

def format_alert(alert: Dict[str, Any]) -> str:
    """Render an alert dict to an HTML-safe Telegram message string.

    The *existing* in-app messages already start with emoji headers
    (e.g. "🟢 Trade opened: …"), so we preserve them verbatim and only
    add lightweight HTML structure: a bold title line + monospace body
    for the key/value pairs.

    Args:
        alert: The dict published to ``trinity:alerts`` — fields
            id, timestamp, severity, type, message, symbol, exchange.

    Returns:
        An HTML-escaped, Telegram-formatted message (parse_mode=HTML).
    """
    message = (alert.get("message") or "").strip()
    symbol = (alert.get("symbol") or "").strip()
    exchange = (alert.get("exchange") or "").strip()
    kind = (alert.get("type") or "system").strip()
    severity = (alert.get("severity") or "info").strip()

    # Map alert type → (emoji, bold header)
    HEADERS: Dict[str, tuple[str, str]] = {
        "trade_open":    ("🟢", "Trade opened"),
        "trade_close":   ("✅", "Trade closed"),
        "daily_summary": ("📊", "Daily summary"),
        "funding":       ("💰", "Funding received"),
        "liquidation":   ("🚨", "Liquidation risk"),
    }
    emoji, title = HEADERS.get(kind, ("ℹ️", kind.replace("_", " ").title()))

    # Severity prefix for non-trade alerts
    if severity == "critical" and kind not in HEADERS:
        emoji = "🚨"
    elif severity == "warning" and kind not in HEADERS:
        emoji = "⚠️"

    # Body: Everything after the leading emoji in `message` (the bot's
    # existing line). If the message doesn't start with emoji, include as-is.
    body = message
    for lead in ("🟢 ", "🔴 ", "✅ ", "⚠️ ", "🚨 ", "ℹ️ "):
        if body.startswith(lead):
            body = body[len(lead):]
            break
    body = html.escape(body)

    # Optional meta tags
    meta_parts = []
    if symbol:
        meta_parts.append(f"<code>{html.escape(symbol)}</code>")
    if exchange:
        meta_parts.append(html.escape(exchange))
    meta_line = "  ·  ".join(meta_parts)

    lines = [f"{emoji} <b>{html.escape(title)}</b>"]
    if body:
        lines.append(body)
    if meta_line:
        lines.append(meta_line)

    text = "\n".join(lines)
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[: _MAX_MESSAGE_CHARS - 3] + "..."
    return text


# ── Notifier ─────────────────────────────────────────────────────

class TelegramNotifier:
    """Thin async client. Create once per bot process; call close() on exit."""

    def __init__(self, config: "TelegramConfig") -> None:
        self._cfg = config
        self._session: Optional[aiohttp.ClientSession] = None

    # Session is created lazily on first send so we don't hold a socket
    # when Telegram is disabled (e.g. in tests).
    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SECS),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ── Silent-hours logic ──────────────────────────────────────
    def _is_quiet_now(self) -> bool:
        fr = self._cfg.silent_from_hour
        to = self._cfg.silent_until_hour
        if fr is None or to is None:
            return False
        # Interpret in configured tz; fall back to system local if tz unknown.
        try:
            from zoneinfo import ZoneInfo  # stdlib
            tz = ZoneInfo(self._cfg.daily_summary_tz)
            now_h = _dt.datetime.now(tz).hour
        except Exception:
            now_h = _dt.datetime.now().hour
        if fr == to:
            return False
        if fr < to:   # e.g. 22..08 when fr=22, to=8 is a wrapping range; that path handled below
            return fr <= now_h < to
        # Wrap-around: fr=22, to=8 → quiet when hour ≥ 22 or hour < 8
        return now_h >= fr or now_h < to

    # ── Public API ──────────────────────────────────────────────

    async def send(self, text: str, silent: bool = False) -> bool:
        """Send a message. Returns True on HTTP 2xx, False on any failure.

        Never raises. Callers should treat the return value as advisory only.
        """
        if not self._cfg.enabled:
            return False
        token = self._cfg.bot_token_plaintext
        if not token:
            return False

        payload = {
            "chat_id": self._cfg.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent or self._is_quiet_now(),
        }

        try:
            session = await self._session_get()
            url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
            async with session.post(url, json=payload) as resp:
                if resp.status == 429:
                    data = await resp.json()
                    retry = data.get("parameters", {}).get("retry_after", 0)
                    logger.warning(
                        "Telegram rate-limited; retry_after=%ss. Dropping message.",
                        retry,
                    )
                    return False
                if resp.status >= 400:
                    body = await resp.text()
                    # Mask token if it leaked into an error body (unlikely
                    # but belt-and-suspenders).
                    safe_body = body.replace(token, "***") if token else body
                    logger.warning("Telegram %s: %s", resp.status, safe_body[:220])
                    return False
                return True
        except asyncio.TimeoutError:
            logger.debug("Telegram send timed out")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("Telegram send failed: %s", exc)
            return False

    async def self_test(self) -> bool:
        """Send a one-off "bot online" ping. Logs a loud warning on failure
        so misconfigured credentials surface at startup, not at first trade."""
        if not self._cfg.enabled:
            logger.info("Telegram notifier disabled (no token/chat_id).")
            return False
        ok = await self.send(
            "🚀 <b>RateBridge</b> online\n"
            "Notifications ready for trade_open, trade_close, daily_summary."
        )
        if not ok:
            logger.warning(
                "⚠️  Telegram self-test FAILED. Notifications will NOT work. "
                "Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
            )
        else:
            logger.info("Telegram self-test OK.")
        return ok

    # ── Integration helper ──────────────────────────────────────

    async def send_alert(self, alert: Dict[str, Any]) -> bool:
        """Convenience: format + send in one call. Honors config switches.

        Returns False (no-op) when the matching `notify_*` flag is disabled.
        """
        if not self._cfg.enabled:
            return False
        kind = alert.get("type")
        if kind == "trade_open" and not self._cfg.notify_trade_open:
            return False
        if kind == "trade_close" and not self._cfg.notify_trade_close:
            return False
        if kind == "daily_summary" and not self._cfg.notify_daily_summary:
            return False
        # Unknown or untyped alerts go through when enabled at all — they're
        # typically rare (system errors) and worth pushing.
        text = format_alert(alert)
        return await self.send(text)
