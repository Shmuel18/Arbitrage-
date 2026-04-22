"""
AI trading assistant — answers natural-language questions about RateBridge.

Supports two providers:
  * Anthropic Claude (paid but cheap, better tool use)
  * Google Gemini   (free tier: 1M tokens/day, 15 RPM)

The provider is chosen at runtime via AI_PROVIDER. If only one API key is
set, that provider is used automatically.

Environment:
  AI_PROVIDER         'anthropic' | 'gemini' | 'auto' (default 'auto')
  ANTHROPIC_API_KEY   for Claude
  GEMINI_API_KEY      for Gemini (get: https://aistudio.google.com/apikey)
  AI_MODEL            optional override (anthropic: claude-sonnet-4-5,
                      gemini: gemini-2.5-flash)
  AI_MAX_TOKENS       default 1024
  AI_LANG             'auto' | 'he' | 'en' (default 'auto')
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("trinity.ai_assistant")

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

try:
    from anthropic import AsyncAnthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import google.generativeai as genai  # type: ignore[import-not-found]
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False


# ── Tool definitions (exposed to the model) ──────────────────────
_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_status",
        "description": "Get the current bot status: running state, connected exchanges, active positions count, and today's summary stats (total PnL, total trades, win rate).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_balances",
        "description": "Get USDT balances on each exchange plus the total across all exchanges.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_open_positions",
        "description": "Get all currently open trading positions with their entry price, current PnL, and exit target.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_trades",
        "description": "Get the most recently closed trades (oldest→newest). Shows PnL, duration, and exit reason for each.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent trades to fetch (1-50). Default 10.",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "get_top_opportunities",
        "description": "Get the top arbitrage opportunities the scanner is currently seeing. Even non-qualifying ones are useful for showing why the bot isn't trading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "get_pnl_summary",
        "description": "Get cumulative PnL and trade counts for a period. Useful for 'how much did I make today/this week?' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Window size in hours (e.g. 24 for today, 168 for last 7 days). Default 24.",
                    "default": 24,
                },
            },
        },
    },
]


# ── Tool implementations ─────────────────────────────────────────

async def _tool_get_status(redis: "RedisClient") -> Dict[str, Any]:
    status_raw = await redis.get("trinity:status")
    summary_raw = await redis.get("trinity:summary")
    status = json.loads(status_raw) if status_raw else {}
    summary = json.loads(summary_raw) if summary_raw else {}
    return {"status": status, "summary": summary}


async def _tool_get_balances(redis: "RedisClient") -> Dict[str, Any]:
    raw = await redis.get("trinity:balances")
    return json.loads(raw) if raw else {}


async def _tool_get_open_positions(redis: "RedisClient") -> List[Dict[str, Any]]:
    raw = await redis.get("trinity:positions")
    data = json.loads(raw) if raw else {}
    positions = data.get("positions", []) if isinstance(data, dict) else []
    # Trim noisy fields to keep context small
    return [
        {
            k: v
            for k, v in pos.items()
            if k in (
                "symbol", "long_exchange", "short_exchange",
                "mode", "tier", "entry_time",
                "net_pnl_pct", "price_pnl_pct", "funding_net_pct",
                "size_usd", "notional_usd", "hold_minutes",
                "funding_collected_usd", "next_funding_ms",
                "entry_net_pct", "current_net_pct",
            )
        }
        for pos in positions[:10]
    ]


async def _tool_get_recent_trades(redis: "RedisClient", limit: int = 10) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 50))
    raws = await redis.lrange("trinity:trades:recent", 0, limit - 1)
    trades: List[Dict[str, Any]] = []
    for r in raws:
        try:
            t = json.loads(r) if isinstance(r, (str, bytes)) else r
        except Exception:
            continue
        trades.append({
            k: t.get(k)
            for k in (
                "symbol", "mode", "tier", "opened_at", "closed_at",
                "hold_minutes", "net_profit_usd", "profit_pct",
                "exit_reason", "long_exchange", "short_exchange",
            )
            if k in t
        })
    return trades


async def _tool_get_top_opportunities(redis: "RedisClient", limit: int = 5) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 20))
    raw = await redis.get("trinity:opportunities")
    data = json.loads(raw) if raw else {}
    opps = data.get("opportunities", []) if isinstance(data, dict) else []
    trimmed = []
    for o in opps[:limit]:
        trimmed.append({
            k: o.get(k)
            for k in (
                "symbol", "long_exchange", "short_exchange",
                "mode", "tier", "qualified",
                "net_pct", "funding_spread_pct", "price_spread_pct",
                "next_funding_ms", "min_interval_hours",
            )
            if k in o
        })
    return trimmed


async def _tool_get_pnl_summary(redis: "RedisClient", hours: int = 24) -> Dict[str, Any]:
    hours = max(1, min(hours, 24 * 365))
    cutoff_ms = int((_dt.datetime.utcnow() - _dt.timedelta(hours=hours)).timestamp() * 1000)

    # Try using the timeseries list if available
    raws = await redis.lrange("trinity:pnl:timeseries", 0, 1000)
    total_pnl = 0.0
    trade_count = 0
    wins = 0
    for r in raws:
        try:
            t = json.loads(r) if isinstance(r, (str, bytes)) else r
        except Exception:
            continue
        ts = t.get("closed_at_ms") or t.get("timestamp_ms") or 0
        if ts < cutoff_ms:
            continue
        pnl = float(t.get("net_profit_usd") or 0)
        total_pnl += pnl
        trade_count += 1
        if pnl > 0:
            wins += 1

    win_rate = (wins / trade_count) if trade_count else None
    return {
        "window_hours": hours,
        "total_pnl_usd": round(total_pnl, 4),
        "trade_count": trade_count,
        "wins": wins,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
    }


# ── Main entrypoint ──────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Heuristic: if any Hebrew char, respond in Hebrew; else English."""
    return "he" if re.search(r"[\u0590-\u05FF]", text) else "en"


def _resolve_provider() -> str:
    """Pick AI provider based on env vars. 'auto' = prefer whichever key exists."""
    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    has_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())
    if provider == "auto":
        if has_anthropic:
            return "anthropic"
        if has_gemini:
            return "gemini"
        return "none"
    return provider


async def answer_question(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    lang: Optional[str] = None,
) -> str:
    """Answer a natural-language question, dispatching to the configured provider."""
    provider = _resolve_provider()
    if provider == "none":
        return (
            "🤖 AI assistant is not configured. Set either "
            "<code>ANTHROPIC_API_KEY</code> (Claude, paid) or "
            "<code>GEMINI_API_KEY</code> (free) in .env."
        )

    lang = lang or os.getenv("AI_LANG", "auto")
    if lang == "auto":
        lang = _detect_language(question)

    if provider == "gemini":
        return await _answer_with_gemini(question, redis, api_key, model, max_tokens, lang)
    return await _answer_with_anthropic(question, redis, api_key, model, max_tokens, lang)


async def _answer_with_anthropic(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str],
    model: Optional[str],
    max_tokens: Optional[int],
    lang: str,
) -> str:
    """Answer using Claude + native tool use."""
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return "🤖 ANTHROPIC_API_KEY is not set."
    if not _ANTHROPIC_AVAILABLE:
        return "🤖 The `anthropic` Python SDK is not installed."

    model = model or os.getenv("AI_MODEL", "claude-sonnet-4-5")
    max_tokens = max_tokens or int(os.getenv("AI_MAX_TOKENS", "1024"))

    client = AsyncAnthropic(api_key=api_key)

    system_prompt = (
        "You are the RateBridge trading bot's AI assistant. RateBridge is a "
        "delta-neutral funding-rate arbitrage engine running on Binance, "
        "Bybit, KuCoin, Gate.io, and Bitget. It takes opposing positions "
        "across exchanges to capture funding-rate differentials.\n\n"
        "You answer user questions about the bot's state, performance, "
        "positions, and opportunities by calling the provided tools to fetch "
        "live data. Always call a tool first — never guess.\n\n"
        "Keep answers concise (2-5 short lines). Use emojis sparingly. "
        "When showing money amounts, use $X.XX format. When showing "
        "percentages, use X.XX% format.\n\n"
        f"Respond in {'Hebrew (עברית)' if lang == 'he' else 'English'}. "
        "Use Telegram HTML tags only: <b>, <i>, <code>. No markdown."
    )

    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": question},
    ]

    tool_impls: Dict[str, Callable[..., Awaitable[Any]]] = {
        "get_status":            lambda **kw: _tool_get_status(redis),
        "get_balances":          lambda **kw: _tool_get_balances(redis),
        "get_open_positions":    lambda **kw: _tool_get_open_positions(redis),
        "get_recent_trades":     lambda **kw: _tool_get_recent_trades(redis, **kw),
        "get_top_opportunities": lambda **kw: _tool_get_top_opportunities(redis, **kw),
        "get_pnl_summary":       lambda **kw: _tool_get_pnl_summary(redis, **kw),
    }

    # Tool-use loop (up to 5 rounds to prevent runaway token spend)
    for _round in range(5):
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=_TOOLS,
            messages=messages,
        )

        # Assistant may have text blocks, tool_use blocks, or both.
        assistant_content = resp.content
        tool_calls = [b for b in assistant_content if b.type == "tool_use"]

        # If no tool calls, we're done — return the text.
        if not tool_calls:
            text_parts = [b.text for b in assistant_content if b.type == "text"]
            return "".join(text_parts).strip() or "🤖 (empty response)"

        # Execute each tool and prepare tool_result messages for next turn.
        messages.append({"role": "assistant", "content": assistant_content})
        tool_results: List[Dict[str, Any]] = []
        for call in tool_calls:
            try:
                impl = tool_impls.get(call.name)
                if impl is None:
                    result = {"error": f"unknown tool {call.name}"}
                else:
                    kwargs = call.input or {}
                    result = await impl(**kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.exception("AI tool %s failed", call.name)
                result = {"error": str(exc)}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(result, default=str)[:8000],
            })
        messages.append({"role": "user", "content": tool_results})

    return "🤖 I spent too many tool-call rounds and couldn't finish. Try rephrasing."


# ── Gemini provider (free) ──────────────────────────────────────

async def _answer_with_gemini(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str],
    model: Optional[str],
    max_tokens: Optional[int],
    lang: str,
) -> str:
    """Answer using Google Gemini (free tier) + native function calling."""
    api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "🤖 GEMINI_API_KEY is not set. Get one free at https://aistudio.google.com/apikey"
    if not _GEMINI_AVAILABLE:
        return (
            "🤖 The `google-generativeai` Python package is not installed. "
            "Add <code>google-generativeai</code> to requirements.txt."
        )

    model_name = model or os.getenv("AI_MODEL", "gemini-2.5-flash")
    max_tokens_i = max_tokens or int(os.getenv("AI_MAX_TOKENS", "1024"))

    # Gemini tool declarations. Gemini's Schema is more restrictive than
    # JSON Schema — it does NOT accept 'default' values inside properties,
    # so we strip them before passing.
    def _strip_defaults(schema: dict) -> dict:
        if not isinstance(schema, dict):
            return schema
        out = {k: v for k, v in schema.items() if k != "default"}
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {k: _strip_defaults(v) for k, v in props.items()}
        return out

    gemini_tools = [{
        "function_declarations": [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": _strip_defaults(t["input_schema"]),
            }
            for t in _TOOLS
        ],
    }]

    genai.configure(api_key=api_key)

    system_instruction = (
        "You are the RateBridge trading bot's AI assistant. RateBridge is a "
        "delta-neutral funding-rate arbitrage engine running on Binance, "
        "Bybit, KuCoin, Gate.io, and Bitget. It takes opposing positions "
        "across exchanges to capture funding-rate differentials.\n\n"
        "Always call a tool first before answering — never guess from memory. "
        "Keep answers concise (2-5 short lines). Use $X.XX for money and "
        "X.XX% for percentages.\n\n"
        f"Respond in {'Hebrew (עברית)' if lang == 'he' else 'English'}. "
        "Use Telegram HTML tags only: <b>, <i>, <code>. No markdown."
    )

    gmodel = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
        tools=gemini_tools,
        generation_config={"max_output_tokens": max_tokens_i, "temperature": 0.2},
    )

    tool_impls: Dict[str, Callable[..., Awaitable[Any]]] = {
        "get_status":            lambda **kw: _tool_get_status(redis),
        "get_balances":          lambda **kw: _tool_get_balances(redis),
        "get_open_positions":    lambda **kw: _tool_get_open_positions(redis),
        "get_recent_trades":     lambda **kw: _tool_get_recent_trades(redis, **kw),
        "get_top_opportunities": lambda **kw: _tool_get_top_opportunities(redis, **kw),
        "get_pnl_summary":       lambda **kw: _tool_get_pnl_summary(redis, **kw),
    }

    # Start a chat so history persists across tool calls.
    chat = gmodel.start_chat(enable_automatic_function_calling=False)
    try:
        # The SDK is sync under the hood; run in a thread pool.
        import asyncio
        response = await asyncio.to_thread(chat.send_message, question)

        # Tool-use loop (up to 5 rounds)
        for _round in range(5):
            fc_calls = []
            for part in response.candidates[0].content.parts:
                fc = getattr(part, "function_call", None)
                if fc is not None and fc.name:
                    fc_calls.append(fc)

            if not fc_calls:
                # No more tool calls → return the final text.
                text = response.text if hasattr(response, "text") else ""
                return (text or "🤖 (empty response)").strip()

            # Execute each tool call and send results back.
            fn_responses = []
            for fc in fc_calls:
                name = fc.name
                args = dict(fc.args) if fc.args else {}
                try:
                    impl = tool_impls.get(name)
                    if impl is None:
                        result = {"error": f"unknown tool {name}"}
                    else:
                        result = await impl(**args)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gemini tool %s failed", name)
                    result = {"error": str(exc)}
                fn_responses.append({
                    "function_response": {
                        "name": name,
                        "response": {"result": json.loads(json.dumps(result, default=str))[:20] if isinstance(result, str) else result},
                    }
                })

            response = await asyncio.to_thread(chat.send_message, fn_responses)

        return "🤖 Too many tool-call rounds. Try rephrasing."
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini request failed")
        return f"🤖 Gemini error: <code>{str(exc)[:300]}</code>"
