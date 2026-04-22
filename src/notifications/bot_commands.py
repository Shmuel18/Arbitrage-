"""Telegram Bot command handler — long-poll loop.

A minimal async loop that:
  1. Long-polls Telegram `getUpdates` (no webhook, no public URL required).
  2. Handles three commands:
       /start   – greeting and menu hint
       /status  – one-shot snapshot of bot state + today's stats
       /menu    – WebApp button that launches the Mini App dashboard

This is intentionally separate from the TelegramNotifier (outbound) so that
inbound commands can fail/reconnect independently without disturbing trade
alerts. It also lets us run WITHOUT commands (set TELEGRAM_MINI_APP_URL=)
and still get notifications.

Design notes:
  * Uses `offset` bookkeeping so updates are acknowledged and not replayed
    after a reconnect.
  * `allowed_user_ids` gates who can issue commands (prevents randoms who
    know the bot username from getting /status output).
  * All network errors caught and logged; the loop never exits except on
    `shutdown_event.set()`.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp

if TYPE_CHECKING:
    from src.core.config import TelegramConfig
    from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.telegram_bot")

_API_BASE = "https://api.telegram.org"
_LONG_POLL_TIMEOUT = 25  # seconds; Telegram allows up to 50


# ── Message formatting ───────────────────────────────────────────

def _format_status(status: Dict[str, Any], summary: Dict[str, Any]) -> str:
    """Render a concise bot status message in HTML."""
    running = status.get("bot_running", False)
    running_icon = "🟢" if running else "🔴"
    running_text = "Running" if running else "Stopped"
    positions = status.get("active_positions", 0)
    exchanges = ", ".join(status.get("connected_exchanges") or []) or "—"

    total_pnl = summary.get("total_pnl", 0) or 0
    total_trades = summary.get("total_trades", 0) or 0
    win_rate = (summary.get("win_rate") or 0) * 100

    lines = [
        f"{running_icon} <b>RateBridge — {running_text}</b>",
        f"Positions: <b>{positions}</b>",
        f"Exchanges: {exchanges}",
        "",
        f"Total PnL: <b>{_sign(total_pnl)}</b>",
        f"Trades: <b>{total_trades}</b>  ·  Win rate: <b>{win_rate:.1f}%</b>",
    ]
    return "\n".join(lines)


def _sign(v: float) -> str:
    return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"


# ── Command handlers ─────────────────────────────────────────────

class _BotCommands:
    def __init__(
        self,
        cfg: "TelegramConfig",
        redis: "RedisClient",
        mini_app_url: Optional[str],
    ) -> None:
        self._cfg = cfg
        self._redis = redis
        self._mini_app_url = mini_app_url
        self._token = cfg.bot_token_plaintext or ""
        self._allowed: List[int] = list(cfg.allowed_user_ids)

    def _is_allowed(self, user_id: int) -> bool:
        # Empty allowlist = allow anyone who can DM the bot. Once the user
        # knows their own ID (printed in reply to /start) they can lock it down.
        return not self._allowed or user_id in self._allowed

    async def dispatch(
        self, session: aiohttp.ClientSession, message: Dict[str, Any],
    ) -> None:
        text = (message.get("text") or "").strip()
        if not text:
            return
        chat_id = message["chat"]["id"]
        user = message.get("from") or {}
        user_id = int(user.get("id", 0))

        if not self._is_allowed(user_id):
            await self._send(session, chat_id,
                f"Access denied. Your Telegram ID is <code>{user_id}</code>.\n"
                f"Add it to <code>TELEGRAM_ALLOWED_USER_IDS</code> to authorize.")
            return

        # ── Slash commands ──
        if text.startswith("/"):
            cmd = text.split()[0].lstrip("/").split("@", 1)[0].lower()
            if cmd == "start":
                await self._cmd_start(session, chat_id, user_id)
            elif cmd == "status":
                await self._cmd_status(session, chat_id)
            elif cmd == "menu":
                await self._cmd_menu(session, chat_id)
            elif cmd == "ask":
                question = text.split(maxsplit=1)[1] if " " in text else ""
                if not question:
                    await self._send(session, chat_id,
                        "Usage: <code>/ask &lt;question&gt;</code>\n"
                        "Example: <code>/ask כמה הרווחתי היום?</code>")
                    return
                await self._cmd_ask(session, chat_id, question)
            else:
                await self._send(session, chat_id,
                    "Unknown command. Try <code>/status</code>, <code>/menu</code>, "
                    "<code>/ask &lt;question&gt;</code>, or just type a question.")
            return

        # ── Natural language → AI assistant ──
        # Any non-slash message from an allowed user is treated as a question.
        await self._cmd_ask(session, chat_id, text)

    async def _cmd_ask(
        self, session: aiohttp.ClientSession, chat_id: int, question: str,
    ) -> None:
        """Forward the question to the AI assistant and reply with its answer.

        Per-chat history is stored in Redis (last 16 messages) so follow-up
        questions like "why?" or "which?" are handled coherently.
        """
        hist_key = f"trinity:ai:history:{chat_id}"
        try:
            # Load prior history (list of JSON strings, oldest first)
            history: list = []
            try:
                raws = await self._redis.lrange(hist_key, 0, 15)
                for r in raws or []:
                    try:
                        history.append(json.loads(r) if isinstance(r, (str, bytes)) else r)
                    except Exception:
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("history load failed: %s", exc)

            from src.notifications.ai_assistant import answer_question
            # Typing indicator
            try:
                await session.post(
                    f"{_API_BASE}/bot{self._token}/sendChatAction",
                    json={"chat_id": chat_id, "action": "typing"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            except Exception:  # noqa: BLE001
                pass
            answer = await answer_question(question, self._redis, history=history)

            # Persist exchange: append user→assistant, trim to 16 messages, 24h TTL
            try:
                await self._redis.rpush(hist_key, json.dumps({"role": "user", "content": question}))
                await self._redis.rpush(hist_key, json.dumps({"role": "assistant", "content": answer}))
                await self._redis.ltrim(hist_key, -16, -1)
                await self._redis.expire(hist_key, 86400)
            except Exception as exc:  # noqa: BLE001
                logger.debug("history persist failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("/ask failed")
            answer = f"🤖 Error: <code>{str(exc)[:200]}</code>"
        await self._send(session, chat_id, answer)

    async def _cmd_start(
        self, session: aiohttp.ClientSession, chat_id: int, user_id: int,
    ) -> None:
        await self._send(
            session, chat_id,
            f"👋 <b>RateBridge bot</b>\n\n"
            f"You'll receive push notifications for trade events and a daily summary.\n\n"
            f"<b>Commands:</b>\n"
            f"  /status — current bot state + today's stats\n"
            f"  /menu — open the dashboard\n\n"
            f"Your Telegram ID: <code>{user_id}</code>",
        )

    async def _cmd_status(
        self, session: aiohttp.ClientSession, chat_id: int,
    ) -> None:
        try:
            status_raw = await self._redis.get("trinity:status")
            summary_raw = await self._redis.get("trinity:summary")
            status = json.loads(status_raw) if status_raw else {}
            summary = json.loads(summary_raw) if summary_raw else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("/status Redis fetch failed: %s", exc)
            await self._send(session, chat_id, "⚠️ Could not read bot state.")
            return
        await self._send(session, chat_id, _format_status(status, summary))

    async def _cmd_menu(
        self, session: aiohttp.ClientSession, chat_id: int,
    ) -> None:
        if not self._mini_app_url:
            await self._send(
                session, chat_id,
                "Mini App is not configured. Set <code>TELEGRAM_MINI_APP_URL</code> "
                "to an HTTPS URL serving the dashboard.",
            )
            return
        # `keyboard` with a WebApp button — Telegram renders a button at
        # the bottom of the chat that opens the URL as a Mini App.
        reply_markup = {
            "keyboard": [[{
                "text": "📊 Open dashboard",
                "web_app": {"url": self._mini_app_url},
            }]],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
        await self._send(session, chat_id,
            "Tap to open the dashboard:",
            reply_markup=reply_markup)

    async def _send(
        self,
        session: aiohttp.ClientSession,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("Telegram sendMessage %s: %s", resp.status, body[:200])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Telegram sendMessage failed: %s", exc)


# ── Long-poll loop ───────────────────────────────────────────────

async def bot_commands_loop(
    cfg: "TelegramConfig",
    redis: "RedisClient",
    shutdown_event: asyncio.Event,
    mini_app_url: Optional[str] = None,
) -> None:
    """Long-poll `getUpdates` and dispatch commands.

    `mini_app_url` is optional — when omitted, /menu replies that it's
    not configured. Useful for running notifications before the Mini App
    is deployed.
    """
    if not cfg.enabled:
        logger.info("Telegram bot command loop disabled (no token/chat_id).")
        return

    token = cfg.bot_token_plaintext
    if not token:
        return
    url = f"{_API_BASE}/bot{token}/getUpdates"
    handler = _BotCommands(cfg, redis, mini_app_url)
    offset: Optional[int] = None

    logger.info("Telegram command loop started (mini_app=%s)",
                "yes" if mini_app_url else "no")

    async with aiohttp.ClientSession() as session:
        while not shutdown_event.is_set():
            params: Dict[str, Any] = {
                "timeout": _LONG_POLL_TIMEOUT,
                "allowed_updates": json.dumps(["message"]),
            }
            if offset is not None:
                params["offset"] = offset

            try:
                timeout = aiohttp.ClientTimeout(total=_LONG_POLL_TIMEOUT + 10)
                async with session.get(url, params=params, timeout=timeout) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning("getUpdates %s: %s", resp.status, body[:200])
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()
            except asyncio.TimeoutError:
                # No updates during the poll window — totally normal.
                continue
            except Exception as exc:  # noqa: BLE001
                logger.debug("getUpdates error: %s", exc)
                await asyncio.sleep(5)
                continue

            if not data.get("ok"):
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                try:
                    await handler.dispatch(session, message)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Command dispatch failed: %s", exc)

    logger.info("Telegram command loop stopped.")
