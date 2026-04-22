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

try:
    from groq import AsyncGroq  # type: ignore[import-not-found]
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


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


def _coerce_int(v, default):
    """Some LLMs (Llama) return ints as strings. Be tolerant."""
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


async def _tool_get_recent_trades(redis: "RedisClient", limit=10) -> List[Dict[str, Any]]:
    limit = _coerce_int(limit, 10)
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


async def _tool_get_top_opportunities(redis: "RedisClient", limit=5) -> List[Dict[str, Any]]:
    limit = _coerce_int(limit, 5)
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


async def _tool_get_pnl_summary(redis: "RedisClient", hours=24) -> Dict[str, Any]:
    hours = _coerce_int(hours, 24)
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
    """Legacy helper — returns the top provider for backwards compat."""
    chain = _provider_chain()
    return chain[0] if chain else "none"


def _provider_chain() -> List[str]:
    """Ordered list of providers to try. Earlier = preferred.

    AI_PROVIDER env var overrides the primary choice. Fallbacks then run
    in this order: gemini → groq → anthropic, skipping any without a key.
    """
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    has_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())
    has_groq = bool(os.getenv("GROQ_API_KEY", "").strip())

    # Priority: Groq first (most reliable tool use with Llama 3.3), then
    # Gemini (sometimes loops on tool calls), then Anthropic (paid).
    default_order = []
    if has_groq:      default_order.append("groq")
    if has_gemini:    default_order.append("gemini")
    if has_anthropic: default_order.append("anthropic")

    primary = os.getenv("AI_PROVIDER", "auto").strip().lower()
    if primary in ("anthropic", "gemini", "groq"):
        chain = [primary] + [p for p in default_order if p != primary]
        # Filter out providers without a key
        return [
            p for p in chain
            if (p == "anthropic" and has_anthropic)
            or (p == "gemini" and has_gemini)
            or (p == "groq" and has_groq)
        ]
    return default_order


async def answer_question(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    lang: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Answer with auto-fallback: tries providers in order until one succeeds.

    `history`: optional list of prior exchanges in OpenAI-style format:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    Providing it lets the model answer follow-up questions coherently
    (e.g., 'why?' or 'which?' referring to the previous reply).

    Falls through on 429/quota errors — so if Gemini's daily quota is
    exhausted, Groq takes over automatically, etc.
    """
    chain = _provider_chain()
    if not chain:
        return (
            "🤖 AI assistant is not configured. Set at least one of:\n"
            "• <code>GEMINI_API_KEY</code> (free, 1500 req/day)\n"
            "• <code>GROQ_API_KEY</code> (free, 14,400 req/day)\n"
            "• <code>ANTHROPIC_API_KEY</code> (paid, high quality)"
        )

    lang = lang or os.getenv("AI_LANG", "auto")
    if lang == "auto":
        lang = _detect_language(question)

    # Sanitize history: keep last 8 exchanges (16 messages max) to bound context.
    hist = history[-16:] if history else []

    last_err: Optional[Exception] = None
    for i, provider in enumerate(chain):
        try:
            if provider == "anthropic":
                return await _answer_with_anthropic(question, redis, api_key, model, max_tokens, lang, hist)
            if provider == "gemini":
                return await _answer_with_gemini(question, redis, api_key, model, max_tokens, lang, hist)
            if provider == "groq":
                return await _answer_with_groq(question, redis, api_key, model, max_tokens, lang, hist)
        except (_ProviderQuotaError, _ProviderToolError) as exc:
            nxt = chain[i + 1] if i + 1 < len(chain) else "none"
            logger.warning("Provider %s failed (%s), trying next (%s): %s",
                           provider, type(exc).__name__, nxt, str(exc)[:150])
            last_err = exc
            continue
        except Exception:  # noqa: BLE001
            # Non-quota errors bubble up — don't silently swap providers
            # when there's a code bug.
            raise

    return (
        "🤖 All AI providers hit their quota. Try again later, or add another "
        "free provider key (GROQ_API_KEY or GEMINI_API_KEY)."
    )


class _ProviderQuotaError(Exception):
    """Raised when a provider responds with 429 / quota exceeded."""
    pass


class _ProviderToolError(Exception):
    """Raised when a provider fails due to tool-schema validation issues."""
    pass


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "quota" in msg or "rate limit" in msg or "too many requests" in msg


def _is_tool_schema_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "tool_use_failed" in msg
        or "tool call validation failed" in msg
        or "parameters for tool" in msg
    )


async def _answer_with_anthropic(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str],
    model: Optional[str],
    max_tokens: Optional[int],
    lang: str,
    history: Optional[List[Dict[str, str]]] = None,
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

    messages: List[Dict[str, Any]] = []
    # Prior conversation (if provided)
    for h in (history or []):
        role = h.get("role")
        content = h.get("content") or h.get("text") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:2000]})
    messages.append({"role": "user", "content": question})

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
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                tools=_TOOLS,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_quota_error(exc):
                raise _ProviderQuotaError(str(exc)) from exc
            raise

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
    history: Optional[List[Dict[str, str]]] = None,
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

    # Free tier comparison (daily request quota):
    #   gemini-2.0-flash      → 1500/day (current default, best balance)
    #   gemini-2.5-flash      → 500/day  (newer but stricter)
    #   gemini-2.5-flash-lite → 1500/day (cheapest+newest, slightly weaker)
    model_name = model or os.getenv("AI_MODEL", "gemini-2.0-flash")
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
        "Bybit, KuCoin, Gate.io, and Bitget.\n\n"
        "Workflow:\n"
        "1. Call ONE tool that answers the user's question.\n"
        "2. READ the returned data.\n"
        "3. Give a final answer in natural language. DO NOT call more tools.\n"
        "4. Only call another tool if the first result is clearly missing what the user asked.\n\n"
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

    # Pre-seed the chat with prior conversation history (if any). Gemini's
    # chat.history accepts OpenAI-style roles renamed: user→"user", assistant→"model".
    gemini_history = []
    for h in (history or []):
        role = h.get("role")
        content = h.get("content") or h.get("text") or ""
        if not content:
            continue
        gm_role = "user" if role == "user" else "model" if role == "assistant" else None
        if gm_role:
            gemini_history.append({"role": gm_role, "parts": [str(content)[:2000]]})

    # Start a chat so history persists across tool calls.
    chat = gmodel.start_chat(
        history=gemini_history,
        enable_automatic_function_calling=False,
    )

    # Model that forbids further tool calls — used as a forced-answer fallback
    # after 2 tool rounds, so we never return "Too many tool-call rounds".
    gmodel_no_tools = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=(
            system_instruction
            + "\n\nIMPORTANT: You MUST answer in text now. Do not request any more tools."
        ),
        generation_config={"max_output_tokens": max_tokens_i, "temperature": 0.2},
    )

    # List of fallback models (try in order on 429/quota errors).
    fallback_models = [
        model_name,
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-2.5-flash-lite",
    ]
    # De-duplicate while preserving order
    tried: List[str] = []
    fallback_unique = [m for m in fallback_models if not (m in tried or tried.append(m))]

    import asyncio

    async def _send_with_fallback(chat_obj, msg):
        last_err = None
        for idx, m in enumerate(fallback_unique):
            try:
                if idx > 0:
                    # Rebuild chat with fallback model
                    new_model = genai.GenerativeModel(
                        model_name=m,
                        system_instruction=system_instruction,
                        tools=gemini_tools,
                        generation_config={"max_output_tokens": max_tokens_i, "temperature": 0.2},
                    )
                    chat_obj = new_model.start_chat(enable_automatic_function_calling=False)
                    logger.info("Gemini falling back to model %s after quota", m)
                return chat_obj, await asyncio.to_thread(chat_obj.send_message, msg)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg_text = str(e).lower()
                if "429" in msg_text or "quota" in msg_text or "rate" in msg_text:
                    continue
                raise
        raise last_err or RuntimeError("All fallback models failed")

    try:
        try:
            chat, response = await _send_with_fallback(chat, question)
        except Exception as exc:  # noqa: BLE001
            if _is_quota_error(exc):
                raise _ProviderQuotaError(str(exc)) from exc
            raise

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
                # Normalize to dict — Gemini's FunctionResponse wants a map.
                if isinstance(result, list):
                    response_payload = {"items": result}
                elif isinstance(result, dict):
                    response_payload = result
                else:
                    response_payload = {"value": str(result)[:2000]}
                fn_responses.append({
                    "function_response": {
                        "name": name,
                        "response": response_payload,
                    }
                })

            try:
                response = await asyncio.to_thread(chat.send_message, fn_responses)
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc):
                    raise _ProviderQuotaError(str(exc)) from exc
                raise

        # Forced-answer fallback: if we ran out of tool rounds, ask the
        # model to summarize what it knows so far, with tools disabled.
        try:
            # Gather the tool data seen in this conversation
            tool_data_summary = json.dumps(
                [{"tool": fc.name, "args": dict(fc.args) if fc.args else {}} for fc in fc_calls],
                default=str,
            )[:4000]
            forced_prompt = (
                f"The user asked: {question}\n\n"
                f"You already have this data from tools: {tool_data_summary}\n\n"
                "Give your FINAL answer now in natural language. Do not ask for more data."
            )
            forced_resp = await asyncio.to_thread(
                gmodel_no_tools.generate_content, forced_prompt
            )
            text = forced_resp.text if hasattr(forced_resp, "text") else ""
            if text and text.strip():
                return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini forced-answer fallback failed: %s", exc)

        return "🤖 Couldn't produce a short answer — try asking more specifically."
    except _ProviderQuotaError:
        # Let the outer dispatcher fall through to the next provider
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini request failed")
        return f"🤖 Gemini error: <code>{str(exc)[:300]}</code>"


# ── Groq provider (free) ───────────────────────────────────────

async def _answer_with_groq(
    question: str,
    redis: "RedisClient",
    api_key: Optional[str],
    model: Optional[str],
    max_tokens: Optional[int],
    lang: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Answer using Groq (free tier, ~14K req/day on Llama 3.3 70B)."""
    api_key = api_key or os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return "🤖 GROQ_API_KEY is not set. Get one free at https://console.groq.com/keys"
    if not _GROQ_AVAILABLE:
        return "🤖 The `groq` Python package is not installed."

    model_name = model or os.getenv("AI_MODEL", "llama-3.3-70b-versatile")
    max_tokens_i = max_tokens or int(os.getenv("AI_MAX_TOKENS", "1024"))

    client = AsyncGroq(api_key=api_key)

    system_prompt = (
        "You are the RateBridge trading bot's AI assistant. RateBridge is a "
        "delta-neutral funding-rate arbitrage engine running on Binance, "
        "Bybit, KuCoin, Gate.io, and Bitget.\n\n"
        "Always call a tool first before answering. Keep answers concise "
        "(2-5 short lines). Use $X.XX for money and X.XX% for percentages.\n\n"
        f"Respond in {'Hebrew (עברית)' if lang == 'he' else 'English'}. "
        "Use Telegram HTML tags only: <b>, <i>, <code>. No markdown."
    )

    # Prepare tool schemas for Groq. Two transformations:
    # 1. Strip 'default' (Groq doesn't allow it in strict validation).
    # 2. Relax integer types to accept both int AND string, because Llama
    #    3.3 often returns `"5"` instead of `5`, and Groq rejects the
    #    call at the API layer before our int-coercion can fire.
    def _prepare_schema(schema: dict) -> dict:
        if not isinstance(schema, dict):
            return schema
        out = {k: v for k, v in schema.items() if k != "default"}
        # Relax integer → also accept string (Llama stringifies ints)
        if out.get("type") == "integer":
            out["type"] = ["integer", "string"]
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {k: _prepare_schema(v) for k, v in props.items()}
        return out

    groq_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": _prepare_schema(t["input_schema"]),
            },
        }
        for t in _TOOLS
    ]

    tool_impls: Dict[str, Callable[..., Awaitable[Any]]] = {
        "get_status":            lambda **kw: _tool_get_status(redis),
        "get_balances":          lambda **kw: _tool_get_balances(redis),
        "get_open_positions":    lambda **kw: _tool_get_open_positions(redis),
        "get_recent_trades":     lambda **kw: _tool_get_recent_trades(redis, **kw),
        "get_top_opportunities": lambda **kw: _tool_get_top_opportunities(redis, **kw),
        "get_pnl_summary":       lambda **kw: _tool_get_pnl_summary(redis, **kw),
    }

    messages = [{"role": "system", "content": system_prompt}]
    # Prior conversation (if provided) — gives context for follow-up questions
    for h in (history or []):
        role = h.get("role")
        content = h.get("content") or h.get("text") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:2000]})
    messages.append({"role": "user", "content": question})

    try:
        MAX_TOOL_ROUNDS = 3  # Force answer after 3 tool rounds
        for _round in range(MAX_TOOL_ROUNDS):
            # On the last round, strip tools entirely so the model must answer.
            is_last_round = (_round == MAX_TOOL_ROUNDS - 1)
            create_kwargs = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens_i,
                "temperature": 0.2,
            }
            if not is_last_round:
                create_kwargs["tools"] = groq_tools
                create_kwargs["tool_choice"] = "auto"
            resp = await client.chat.completions.create(**create_kwargs)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                return (msg.content or "🤖 (empty response)").strip()

            # Append assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })

            # Execute each tool
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                try:
                    impl = tool_impls.get(name)
                    if impl is None:
                        result = {"error": f"unknown tool {name}"}
                    else:
                        result = await impl(**args)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Groq tool %s failed", name)
                    result = {"error": str(exc)}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)[:8000],
                })

        return "🤖 Too many tool-call rounds. Try rephrasing."
    except (_ProviderQuotaError, _ProviderToolError):
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_quota_error(exc):
            raise _ProviderQuotaError(str(exc)) from exc
        if _is_tool_schema_error(exc):
            raise _ProviderToolError(str(exc)) from exc
        logger.exception("Groq request failed")
        return f"🤖 Groq error: <code>{str(exc)[:300]}</code>"
