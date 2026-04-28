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
import time as _time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp

if TYPE_CHECKING:
    from src.core.config import TelegramConfig

logger = logging.getLogger("trinity.telegram")

_TELEGRAM_API_BASE = "https://api.telegram.org"
_HTTP_TIMEOUT_SECS = 10
_MAX_MESSAGE_CHARS = 4096  # Telegram hard limit


# ── Formatter ────────────────────────────────────────────────────

_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"

# Hebrew labels for exit_reason values. Prefix-matched against
# trade._exit_reason; the latter is sometimes "<reason>_<param>"
# (e.g. "profit_target_1.5845pct"), so we match by startswith.
_EXIT_REASON_HE: Dict[str, str] = {
    "profit_target":         "🎯 יעד רווח",
    "basis_recovery":        "✅ basis התאושש",
    "cherry_hard_stop":      "🍒 Cherry hard stop",
    "max_hold_timeout":      "⏰ timeout מקסימלי",
    "negative_funding":      "⚠️ funding שלילי",
    "liquidation_external":  "💥 חיסול ע״י הבורסה",
    "liquidation_risk":      "🚨 סיכון ליקווידציה",
    "manual_close":          "🛑 סגירה ידנית",
    "upgrade_exit":          "⬆️ שדרוג",
    "restart_shutdown":      "🔄 הפעלה מחדש",
    "spread_below_threshold": "📉 ספרד נמוך",
    "no_funding_received":   "⏰ funding לא הגיע",
    "price_spike":           "⚡ price spike",
}

_MODE_LABEL_HE: Dict[str, str] = {
    "pot":         "🍯 POT",
    "cherry_pick": "🍒 CHERRY",
    "nutcracker":  "🥜 NUTCRACKER",
    "hold":        "🤝 HOLD",
}

_TIER_LABEL_HE: Dict[str, str] = {
    "top":     "🏆 מובי",
    "medium":  "📊 בינוני",
    "weak":    "⚡ חלש",
    "adverse": "⚠️ אדברסי",
}


def _exit_reason_he(raw: Any) -> str:
    """Translate a raw exit_reason to Hebrew label, falling back to the raw string."""
    if not raw:
        return ""
    s = str(raw).lower()
    for prefix, label in _EXIT_REASON_HE.items():
        if s == prefix or s.startswith(prefix + "_"):
            return label
    return raw  # unknown — show as-is


def _fmt_countdown(ms_in_future: Any) -> str:
    """Format a future-timestamp (ms) as 'בעוד Xש Yד' / 'בעוד Xד' / '—'."""
    try:
        delta_s = (float(ms_in_future) / 1000) - _time.time()
    except (TypeError, ValueError):
        return "—"
    if delta_s <= 0:
        return "עכשיו"
    delta_min = int(delta_s // 60)
    if delta_min < 60:
        return f"בעוד {delta_min}ד"
    h = delta_min // 60
    m = delta_min % 60
    if m == 0:
        return f"בעוד {h}ש"
    return f"בעוד {h}ש {m}ד"


# Format helpers — kept tiny and pure so they're trivially testable.
def _fmt_money(v: Any, dp: int = 2, signed: bool = False) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f < 0:
        return f"-${abs(f):,.{dp}f}"
    sign = "+" if signed else ""
    return f"{sign}${f:,.{dp}f}"


def _fmt_pct(v: Any, dp: int = 4, signed: bool = True) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if signed and f >= 0 else ""
    return f"{sign}{f:.{dp}f}%"


def _fmt_price(v: Any, dp: int = 6) -> str:
    try:
        return f"${float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_hold(minutes: Any) -> str:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "—"
    if m < 60:
        return f"{m:.0f}m"
    h = int(m // 60)
    rem = int(round(m - h * 60))
    return f"{h}h {rem:02d}m"


def _format_trade_open(p: Dict[str, Any], symbol: str) -> str:
    """Render a trade_open payload into a Telegram HTML card."""
    long_ex = str(p.get("long_exchange") or "—").upper()
    short_ex = str(p.get("short_exchange") or "—").upper()
    long_qty = p.get("long_qty")
    short_qty = p.get("short_qty")
    long_price = p.get("entry_price_long")
    short_price = p.get("entry_price_short")
    long_funding = p.get("long_funding_rate_pct")
    short_funding = p.get("short_funding_rate_pct")
    notional = p.get("notional")
    net_edge = p.get("net_edge_pct")
    spread = p.get("immediate_spread_pct")
    mode = str(p.get("mode") or "").lower()
    tier = str(p.get("entry_tier") or "").lower()
    long_iv = p.get("long_interval_hours")
    short_iv = p.get("short_interval_hours")
    next_funding_ms = p.get("next_funding_ms")

    sym_disp = html.escape(symbol or "—")

    # ── Header ──
    head = f"🟢 <b>עסקה נפתחה</b>\n<code>{sym_disp}</code>"

    # ── Mode + tier sub-row ──
    sub_bits: List[str] = []
    mode_label = _MODE_LABEL_HE.get(mode, mode.upper() if mode else "")
    if mode_label:
        sub_bits.append(f"<b>{mode_label}</b>")
    tier_label = _TIER_LABEL_HE.get(tier, "")
    if tier_label:
        sub_bits.append(f"<b>{tier_label}</b>")
    sub_row = "  ·  ".join(sub_bits) if sub_bits else ""

    # ── Cycles + countdown row ──
    cycle_bits: List[str] = []
    if long_iv and short_iv:
        cycle_bits.append(
            f"🔄 {html.escape(long_ex[:3])} {long_iv}h × {html.escape(short_ex[:3])} {short_iv}h"
        )
    if next_funding_ms:
        cycle_bits.append(f"⏱ funding {_fmt_countdown(next_funding_ms)}")
    cycle_row = "  ·  ".join(cycle_bits) if cycle_bits else ""

    # ── Legs ──
    leg_block = (
        f"🟩 <b>LONG</b>  ·  {html.escape(long_ex)}\n"
        f"     <code>{html.escape(str(long_qty))}</code> @ <code>{_fmt_price(long_price)}</code>"
        f"  ·  funding <b>{_fmt_pct(long_funding, dp=2)}</b>\n"
        f"🟥 <b>SHORT</b>  ·  {html.escape(short_ex)}\n"
        f"     <code>{html.escape(str(short_qty))}</code> @ <code>{_fmt_price(short_price)}</code>"
        f"  ·  funding <b>{_fmt_pct(short_funding, dp=2)}</b>"
    )

    # ── Totals ──
    totals_lines = []
    if notional is not None:
        totals_lines.append(f"💵 נוטיונל לרגל     <b>{_fmt_money(notional)}</b>")
    if spread is not None:
        totals_lines.append(f"📐 ספרד מיידי       <b>{_fmt_pct(spread, dp=2)}</b>")
    if net_edge is not None:
        totals_lines.append(f"🎯 קצה נטו         <b>{_fmt_pct(net_edge, dp=2)}</b>")

    # ── Assemble ──
    parts = [head]
    if sub_row:
        parts.append(sub_row)
    if cycle_row:
        parts.append(cycle_row)
    parts.append(_DIVIDER)
    parts.append(leg_block)
    if totals_lines:
        parts.append(_DIVIDER)
        parts.append("\n".join(totals_lines))
    text = "\n".join(parts)
    return text[:_MAX_MESSAGE_CHARS]


def _format_trade_close(p: Dict[str, Any], symbol: str) -> str:
    """Render a trade_close payload into a Telegram HTML card."""
    long_ex = str(p.get("long_exchange") or "—").upper()
    short_ex = str(p.get("short_exchange") or "—").upper()
    total_pnl = p.get("total_pnl")
    price_pnl = p.get("price_pnl")
    funding_net = p.get("funding_net")
    fees = p.get("fees")
    invested = p.get("invested")
    profit_pct = p.get("profit_pct")
    hold_min = p.get("hold_minutes")
    exit_reason = p.get("exit_reason")
    entry_long = p.get("entry_price_long")
    entry_short = p.get("entry_price_short")
    exit_long = p.get("exit_price_long")
    exit_short = p.get("exit_price_short")
    today_stats = p.get("today_stats") or {}

    try:
        is_win = float(total_pnl) >= 0
    except (TypeError, ValueError):
        is_win = True
    head_emoji = "✅" if is_win else "🔴"
    head_label = "עסקה נסגרה ברווח" if is_win else "עסקה נסגרה בהפסד"

    sym_disp = html.escape(symbol or "—")

    # ── Header with PnL right in the title ──
    head_parts = [f"{head_emoji} <b>{head_label}</b>"]
    if profit_pct is not None:
        head_parts.append(_fmt_pct(profit_pct, dp=3))
    head = (
        "  ·  ".join(head_parts) + "\n"
        f"<code>{sym_disp}</code>"
    )
    if hold_min is not None:
        head += f"  ·  ⏱ {html.escape(_fmt_hold(hold_min))}"

    # ── Top-line PnL ──
    pnl_lines = [f"💰 רווח נטו        <b>{_fmt_money(total_pnl, dp=4, signed=True)}</b>"]
    if profit_pct is not None and invested is not None:
        pnl_lines.append(
            f"📊 תשואה          <b>{_fmt_pct(profit_pct, dp=3)}</b> על {_fmt_money(invested)}"
        )

    # ── Breakdown ──
    breakdown_lines = ["<b>פירוק PnL</b>"]
    if price_pnl is not None:
        breakdown_lines.append(
            f"  💱 מחיר (basis)   {_fmt_money(price_pnl, dp=4, signed=True)}"
        )
    if funding_net is not None:
        breakdown_lines.append(
            f"  💸 מימון נטו      {_fmt_money(funding_net, dp=4, signed=True)}"
        )
    if fees is not None:
        breakdown_lines.append(
            f"  💼 עמלות          -{_fmt_money(abs(float(fees)), dp=4)}"
        )
    has_breakdown = len(breakdown_lines) > 1

    # ── Legs ──
    legs_lines = []
    if entry_long is not None and exit_long is not None:
        legs_lines.append(
            f"🟩 LONG  {html.escape(long_ex):<7s}  "
            f"<code>{_fmt_price(entry_long)} → {_fmt_price(exit_long)}</code>"
        )
    if entry_short is not None and exit_short is not None:
        legs_lines.append(
            f"🟥 SHORT {html.escape(short_ex):<7s}  "
            f"<code>{_fmt_price(entry_short)} → {_fmt_price(exit_short)}</code>"
        )

    # ── Exit reason ──
    reason_line = ""
    if exit_reason:
        reason_he = _exit_reason_he(exit_reason)
        reason_line = f"📌 סיבת יציאה: <b>{html.escape(reason_he)}</b>"

    # ── Today's stats footer ──
    stats_line = ""
    if today_stats and today_stats.get("trade_count"):
        wins = today_stats.get("wins", 0)
        losses = today_stats.get("losses", 0)
        net = today_stats.get("total_pnl", 0.0)
        wr = today_stats.get("win_rate", 0.0)
        try:
            stats_line = (
                f"📈 <b>היום:</b> "
                f"{int(wins)}W/{int(losses)}L  ·  "
                f"{_fmt_money(float(net), dp=2, signed=True)}  ·  "
                f"WR <b>{float(wr) * 100:.0f}%</b>"
            )
        except (TypeError, ValueError):
            stats_line = ""

    # ── Assemble ──
    parts = [head, _DIVIDER, "\n".join(pnl_lines)]
    if has_breakdown:
        parts.append(_DIVIDER)
        parts.append("\n".join(breakdown_lines))
    if legs_lines:
        parts.append(_DIVIDER)
        parts.append("\n".join(legs_lines))
    if reason_line:
        parts.append(_DIVIDER)
        parts.append(reason_line)
    if stats_line:
        parts.append(_DIVIDER)
        parts.append(stats_line)
    text = "\n".join(parts)
    return text[:_MAX_MESSAGE_CHARS]


def _format_generic(alert: Dict[str, Any]) -> str:
    """Fallback for non-trade alert types (system, daily_summary, errors)."""
    message = (alert.get("message") or "").strip()
    symbol = (alert.get("symbol") or "").strip()
    exchange = (alert.get("exchange") or "").strip()
    kind = (alert.get("type") or "system").strip()
    severity = (alert.get("severity") or "info").strip()

    HEADERS: Dict[str, tuple[str, str]] = {
        "daily_summary": ("📊", "סיכום יומי"),
        "funding":       ("💰", "מימון התקבל"),
        "liquidation":   ("🚨", "סיכון לליקווידציה"),
    }
    emoji, title = HEADERS.get(kind, ("ℹ️", kind.replace("_", " ").title()))

    if severity == "critical" and kind not in HEADERS:
        emoji = "🚨"
    elif severity == "warning" and kind not in HEADERS:
        emoji = "⚠️"

    body = message
    for lead in ("🟢 ", "🔴 ", "✅ ", "⚠️ ", "🚨 ", "ℹ️ "):
        if body.startswith(lead):
            body = body[len(lead):]
            break
    body = html.escape(body)

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
    return text[:_MAX_MESSAGE_CHARS]


def format_alert(alert: Dict[str, Any]) -> str:
    """Render an alert dict to an HTML-safe Telegram message string.

    For trade_open / trade_close, the formatter prefers a structured
    `payload` dict (passed via APIPublisher.publish_alert(payload=...))
    and renders a richer card. Other alert types fall through to the
    generic emoji+message format.

    Args:
        alert: The dict published to ``trinity:alerts`` — fields
            id, timestamp, severity, type, message, symbol, exchange,
            payload.

    Returns:
        An HTML-escaped, Telegram-formatted message (parse_mode=HTML).
    """
    kind = (alert.get("type") or "system").strip()
    payload = alert.get("payload") or None
    symbol = (alert.get("symbol") or "").strip()

    if kind == "trade_open" and isinstance(payload, dict):
        return _format_trade_open(payload, symbol)
    if kind == "trade_close" and isinstance(payload, dict):
        return _format_trade_close(payload, symbol)
    return _format_generic(alert)


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

    async def send(
        self,
        text: str,
        silent: bool = False,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a message. Returns True on HTTP 2xx, False on any failure.

        Never raises. Callers should treat the return value as advisory only.

        Args:
            text:         HTML-formatted message body.
            silent:       Suppress notification sound (still delivers).
            reply_markup: Optional inline keyboard / reply keyboard, e.g.
                          ``{"inline_keyboard": [[{"text": "...", "url": "..."}]]}``.
        """
        if not self._cfg.enabled:
            return False
        token = self._cfg.bot_token_plaintext
        if not token:
            return False

        payload: Dict[str, Any] = {
            "chat_id": self._cfg.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent or self._is_quiet_now(),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

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
        Trade-open / trade_close alerts get a Dashboard inline button so
        the user can jump from the notification to the live UI in one tap.
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
        reply_markup = self._build_alert_keyboard(kind)
        return await self.send(text, reply_markup=reply_markup)

    def _build_alert_keyboard(self, kind: Optional[str]) -> Optional[Dict[str, Any]]:
        """Build an inline keyboard for trade-related alerts (URL only — no
        callbacks, so a stray tap can never trigger a destructive action).

        Returns ``None`` for alert types that shouldn't carry a keyboard
        (system / generic / daily_summary already include their own context).
        """
        if kind not in ("trade_open", "trade_close"):
            return None
        url = (
            getattr(self._cfg, "dashboard_url", None)
            or getattr(self._cfg, "mini_app_url", None)
            or ""
        )
        url = str(url).strip()
        if not url:
            return None
        return {
            "inline_keyboard": [
                [{"text": "🔗 לדאשבורד", "url": url}],
            ]
        }
