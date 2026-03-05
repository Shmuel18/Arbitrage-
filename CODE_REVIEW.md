# Trinity Bot — Code Review (Staff-Level)

**Reviewer:** GitHub Copilot  
**Date:** 2025  
**Scope:** Full stack — Python backend (bot + API) + React/TypeScript frontend  
**LOC reviewed:** ~12,000

---

## Executive Summary

**Score: 7.5 / 10**

This is a surprisingly mature solo project. The trading logic is well-reasoned, the error handling is above-average for a crypto bot, and the frontend has professional-grade WebSocket optimization. The code shows clear signs of battle-testing — orphan handling, partial-fill correction, zero-fill guards, rate-direction re-verification — these are all features that emerge from painful production incidents.

That said, there are **3 bugs that can lose money or crash the bot under load**, several architectural-debt items that will slow future development, and a few performance wins left on the table.

---

## 🔴 RED FLAGS — Bugs That Can Lose Money or Crash

### 1. `RuntimeError: dictionary changed size during iteration` (CRASH)

**File:** `main.py`, `publish_status_loop()`  
**Severity:** 🔴 Critical — will crash the status publisher, causing stale dashboard data

```python
# main.py — publish_status_loop
for tid, trade in controller._active_trades.items():   # ← dict view, NOT a copy
    ...
    _lt = await _la.get_ticker(trade.symbol)           # ← yields to event loop
    ...
```

Between any `await` in this loop, another coroutine (entry mixin, close mixin, monitor loop) can add/remove from `_active_trades`. Python's `dict.items()` returns a **view** that references the live dict — if the dict changes size mid-iteration, Python raises `RuntimeError`.

The same bug exists in the **second loop** in the same function that computes `unrealized_pnl`:

```python
for tid, trade in controller._active_trades.items():   # ← same bug
    la = mgr.get(trade.long_exchange)
    long_positions = await la.get_positions(trade.symbol)  # ← yields
```

**Fix:**

```python
for tid, trade in list(controller._active_trades.items()):  # snapshot
```

Note: `_exit_monitor_loop` in `_monitor_mixin.py` correctly uses `list(self._active_trades.items())`. This pattern should be uniform everywhere.

---

### 2. PnL Timeseries Drops Duplicate Values (SILENT DATA LOSS)

**File:** `_close_mixin.py` line ~near end, `main.py`  
**Severity:** 🟠 High — silently under-counts realized PnL

```python
await self._redis._client.zadd(
    "trinity:pnl:timeseries",
    {str(pnl_value): ts},      # member = "$-0.13", score = timestamp
)
```

Redis sorted sets deduplicate by **member**. If two trades close with the same PnL value (e.g., both `$-0.13`), only the **last** entry survives. The realized PnL sum will miss the dropped trade.

**Fix:** Include the trade ID in the member to guarantee uniqueness:

```python
import json
member = json.dumps({"trade_id": trade.trade_id, "pnl": float(total_pnl)})
await self._redis._client.zadd("trinity:pnl:timeseries", {member: ts})
```

And update the reader in `publish_status_loop`:

```python
for member, _score in closed_pnl:
    data = json.loads(member)
    realized_pnl += data["pnl"]
```

---

### 3. Direct `_client` Access Bypasses Key Prefix (DATA ISOLATION BUG)

**Files:** `main.py`, `_close_mixin.py`  
**Severity:** 🟠 High — keys written without `trinity:` prefix will collide in shared Redis

The `RedisClient` class applies a key prefix (`trinity:`) via its public methods. But multiple files bypass it:

```python
# _close_mixin.py
await self._redis._client.zadd("trinity:pnl:timeseries", ...)  # manually re-adding prefix
await self._redis.zadd("trinity:trades:history", ...)           # uses a method that may not exist

# main.py
await redis._client.zrangebyscore("trinity:pnl:timeseries", ...)
await redis._client.zadd("trinity:pnl:running", ...)
await redis._client.zremrangebyscore("trinity:pnl:running", ...)
```

Problems:

- The prefix is hardcoded as `"trinity:"` in the caller instead of using `self._redis.prefix`
- Direct `_client` access is a private API violation — if RedisClient switches to a connection pool or adds instrumentation, these calls won't benefit
- `self._redis.zadd(...)` is called but `zadd` may not be a method on `RedisClient` (potential `AttributeError`)

**Fix:** Add `zadd`, `zrangebyscore`, `zremrangebyscore` methods to `RedisClient` that apply the prefix.

---

## 🟡 PERFORMANCE & LATENCY

### 4. Sequential REST Calls in Status Publisher (LATENCY)

**File:** `main.py`, `publish_status_loop()`

For each active trade, the loop makes **4 sequential REST calls**: `get_ticker()` × 2 + `get_positions()` × 2. With 3 open trades, that's **12 sequential API calls per 5-second cycle**.

```python
for tid, trade in controller._active_trades.items():
    _lt = await _la.get_ticker(trade.symbol)    # ~200ms
    _st = await _sa.get_ticker(trade.symbol)    # ~200ms
    ...
    long_positions = await la.get_positions(trade.symbol)   # ~200ms
    short_positions = await sa.get_positions(trade.symbol)  # ~200ms
```

At 200ms per call, 3 trades = ~2.4s. The 5-second loop budget is nearly consumed by REST latency.

**Fix:** Gather all independent calls:

```python
trades_snapshot = list(controller._active_trades.items())
# Batch all ticker fetches in parallel
ticker_tasks = {}
for tid, trade in trades_snapshot:
    la, sa = mgr.get(trade.long_exchange), mgr.get(trade.short_exchange)
    ticker_tasks[(tid, 'long')] = la.get_ticker(trade.symbol)
    ticker_tasks[(tid, 'short')] = sa.get_ticker(trade.symbol)

results = await asyncio.gather(*ticker_tasks.values(), return_exceptions=True)
ticker_map = dict(zip(ticker_tasks.keys(), results))
```

**Impact:** 12 sequential calls → 2 parallel batches (~400ms total). 6× speedup.

---

### 5. `ExchangeManager.all()` Returns a Copy Every Call

**File:** `adapter.py`, `ExchangeManager.all()`

```python
def all(self) -> Dict[str, ExchangeAdapter]:
    return dict(self._adapters)    # new dict on EVERY call
```

Called from: `scanner.scan_all()`, `risk_guard._check_delta()`, `publish_status_loop()`, `scanner.start()` — at least 4× every 5-10 seconds.

The adapters dict never changes after startup (verified exchanges are locked in). Creating a copy is wasteful.

**Fix:** Return the internal dict directly (it's not mutated after `verify_all()`):

```python
def all(self) -> Dict[str, ExchangeAdapter]:
    return self._adapters  # or use types.MappingProxyType for safety
```

---

### 6. Scanner Creates Coroutine Objects Before Semaphore (MEMORY)

**File:** `scanner.py`, `scan_all()`

```python
scan_tasks = [
    self._scan_symbol(symbol, adapters, exchange_ids, cooled_symbols)
    for symbol in symbol_list
]   # ← creates ~500 coroutine objects immediately

async def bounded_scan(task):
    async with semaphore:
        return await task

gathered = await asyncio.gather(*[bounded_scan(t) for t in scan_tasks], ...)
```

This creates all 500 coroutines eagerly, then wraps each in another coroutine. The semaphore limits concurrency correctly, but the eager creation wastes memory.

**Fix:** Use `asyncio.Semaphore` directly without pre-creating:

```python
async def bounded_scan(symbol):
    async with semaphore:
        return await self._scan_symbol(symbol, adapters, exchange_ids, cooled_symbols)

gathered = await asyncio.gather(
    *[bounded_scan(s) for s in symbol_list],
    return_exceptions=True,
)
```

---

## 🟢 ROBUSTNESS & ERROR HANDLING — What's Done Right

These patterns are **above average** and show production battle-testing:

| Pattern                          | Location                   | Notes                                                            |
| -------------------------------- | -------------------------- | ---------------------------------------------------------------- |
| Orphan leg recovery              | `_entry_mixin.py`          | 3 retries + critical alert + cooldown                            |
| Zero-fill guard                  | `_entry_mixin.py`          | Catches accepted-but-unfilled orders on both legs                |
| Delta correction                 | `_entry_mixin.py`          | Auto-trims excess when fills don't match                         |
| Rate re-verification             | `_entry_mixin.py`          | Rechecks funding direction before placing orders                 |
| Position-based fill verification | `adapter.py`               | Fallback when `fetchOrder()` fails (Bybit)                       |
| Risk guard incomplete-data abort | `guard.py`                 | Skips delta check if any exchange fails                          |
| TOCTOU guard                     | `controller.py`            | `_symbols_entering` set prevents duplicate entries               |
| Interval change confirmation     | `adapter.py`               | Requires 2 consecutive polls to accept interval change           |
| Clock re-sync                    | `adapter.py`               | Every 5 min to prevent timestamp-ahead errors                    |
| Kraken funding parser patch      | `adapter.py`               | Fixes ccxt string comparison bug                                 |
| Grace period after trade open    | `guard.py`                 | 60s delta-check skip to avoid false breach                       |
| f-string guard for DEBUG         | `adapter.py`, `scanner.py` | `if logger.isEnabledFor(logging.DEBUG)` — avoids formatting cost |

These are exactly the kinds of guards that distinguish a toy project from a production system.

---

## 🏗️ ARCHITECTURE & CLEAN CODE

### 7. `main.py` — 434-Line God Function

The `publish_status_loop()` inner function is ~200 lines and handles:

- Bot status publishing
- Balance fetching
- Position data enrichment (live spread, unrealized PnL, pending funding)
- Running PnL computation
- PnL chart data assembly

**Fix:** Extract into a `StatusPublisher` class with methods:

```python
class StatusPublisher:
    async def publish_cycle(self):
        await self._publish_status()
        await self._publish_balances()
        await self._publish_positions()
        await self._publish_pnl()
```

### 8. Mixin Inheritance Chain — Acceptable but Fragile

```
ExecutionController → _EntryMixin → _MonitorMixin → _ExitLogicMixin → _CloseMixin → _UtilMixin
```

This is a linear mixin chain, not a diamond. It works, but:

- All mixins access `self._active_trades`, `self._cfg`, `self._redis`, etc. via implicit `self`
- No interface/protocol defining what attributes a mixin expects → IDE autocomplete breaks
- Adding a new mixin requires understanding the entire chain

**Suggestion:** Add a `Protocol` class defining the shared state:

```python
class ControllerProtocol(Protocol):
    _active_trades: Dict[str, TradeRecord]
    _cfg: Config
    _redis: RedisClient
    _exchanges: ExchangeManager
    ...
```

### 9. Dead Code

| File                                           | Issue                                                   |
| ---------------------------------------------- | ------------------------------------------------------- |
| `TradesHistory.tsx`                            | Marked `@deprecated`, not imported anywhere. Delete it. |
| `calculate_funding_edge()`                     | Backward-compat alias. If nothing calls it, delete.     |
| `annualized_pct` in `calculate_funding_spread` | Always returns `Decimal("0")`. Dead field.              |

### 10. Config Validation Gap

`config.py` has excellent Pydantic validation but `config.yaml` values like `leverage: 5` could be `leverage: 500` and pass validation since `max_leverage` defaults to 125 but the clamp only happens at runtime in `ensure_trading_settings()`. The Pydantic model should enforce:

```python
leverage: int = Field(default=5, ge=1, le=125)
```

---

## 🔒 CONCURRENCY & SCALABILITY

### 11. No Watchdog for Background Tasks

Background tasks (funding pollers, price pollers, WebSocket watchers) are fire-and-forget:

```python
task = asyncio.create_task(self._batch_funding_poll_loop(eligible))
self._ws_tasks.append(task)
```

If a task crashes with an unhandled exception, it dies silently. The cache goes stale, the scanner sees old rates, and bad trades happen.

**Fix:** Add a watchdog:

```python
async def _supervised_task(self, coro, name: str):
    while True:
        try:
            await coro()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Task {name} crashed, restarting in 5s: {e}")
            await asyncio.sleep(5)
```

### 12. No Concurrency Limit on Exchange API Calls

During entry, the bot calls `ensure_trading_settings()` on both exchanges in parallel (good), then places orders sequentially (necessary for sync-fire). But balance fetches and ticker fetches across the entire system have no shared rate limiter.

The `enableRateLimit: True` ccxt option only throttles per-exchange. If the scanner, status publisher, risk guard, and exit monitor all hit the same exchange simultaneously, the aggregated rate may trigger IP-level rate limits.

**Suggestion:** Add a per-exchange `asyncio.Semaphore(3)` for all outbound calls.

---

## 🖥️ FRONTEND REVIEW

### What's Done Well

1. **WebSocket reference-equality optimization** — The `setData` handler in `App.tsx` carefully compares old vs new data for every field (status, balances, positions, trades, logs, opportunities) and only swaps the reference when something changed. This prevents useless React re-renders. **Staff-level optimization.**

2. **Error boundary** — Class-based ErrorBoundary with reload button. Good.

3. **`useMemo` on filtered opportunity lists** — Prevents re-computation on every render.

4. **Scroll-spy** — IntersectionObserver-based active-section tracking in Dashboard. Nice UX touch.

5. **SVG PnL chart** — Custom-drawn with zero-line split coloring (green above, red below). Clean.

### Frontend Issues

| #   | Issue                                                         | Severity | File                                                                                                    |
| --- | ------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------- |
| 13  | `TradesHistory.tsx` is dead code                              | Low      | Delete it                                                                                               |
| 14  | No loading/skeleton states for Dashboard sections             | Low      | `PositionsTable`, `RightPanel` show "no data" instantly before first fetch                              |
| 15  | `fetchAll()` dependency on `pnlHours` re-creates the interval | Medium   | Changing PnL timeframe restarts the 5s polling timer                                                    |
| 16  | `JSON.stringify` in WebSocket equality check                  | Low      | `JSON.stringify(d.status.connected_exchanges)` on every WS message — use a length + first-element check |
| 17  | SVG clipPath IDs are non-unique (`clip-above`, `clip-below`)  | Low      | If AnalyticsPanel is rendered twice (unlikely), IDs collide                                             |
| 18  | No debounce on PnL timeframe buttons                          | Low      | Rapid clicks trigger multiple `fetchAll()` calls                                                        |

---

## 🧠 SENIOR WISDOM — Nitpicks & Tasteful Improvements

### 19. Use `Decimal` Constants

```python
# Current (creates new Decimal on every call):
immediate_spread_pct = (immediate_long_pnl + immediate_short_pnl) * Decimal("100")

# Better (module-level constant):
_D100 = Decimal("100")
_D0 = Decimal("0")
```

This is called thousands of times per scan. Decimal construction from string is not free.

### 20. Structured Logging Over f-strings

The code uses `get_logger()` with `extra={}` dicts — good. But the main message is still an f-string:

```python
logger.info(
    f"Trade opened: {trade_id} {opp.symbol} L={opp.long_exchange}...",
    extra={"trade_id": trade_id, ...}
)
```

The f-string is formatted even if the log level is disabled (though INFO is always enabled). The `extra` dict duplicates the same data. Consider using structured logging (e.g., `structlog`) where the message is a template and all data is in fields.

### 21. Type Narrowing with `TypeGuard`

Several places check `if not long_spec or not short_spec: return None` then proceed to use them. Adding `TypeGuard` would help IDE inference:

```python
from typing import TypeGuard
def _both_specs(a: Optional[T], b: Optional[T]) -> TypeGuard[tuple[T, T]]:
    return a is not None and b is not None
```

### 22. Test Coverage

The `tests/` directory has test files for adapter, blacklist, calculator, contracts, controller, guard, helpers, publisher, redis_client, scanner, sizer. This is excellent coverage at the unit level.

**Missing:** Integration tests that verify the full scan → entry → monitor → exit pipeline with mocked exchanges.

---

## Summary Table

| #     | Issue                                    | Severity    | Effort  | Category     |
| ----- | ---------------------------------------- | ----------- | ------- | ------------ |
| 1     | Dict iteration crash in status publisher | 🔴 Critical | 5 min   | Bug          |
| 2     | PnL timeseries drops duplicate values    | 🟠 High     | 15 min  | Bug          |
| 3     | Direct `_client` access bypasses prefix  | 🟠 High     | 30 min  | Bug          |
| 4     | Sequential REST calls in status loop     | 🟡 Medium   | 30 min  | Performance  |
| 5     | `ExchangeManager.all()` copies dict      | 🟢 Low      | 2 min   | Performance  |
| 6     | Eager coroutine creation in scanner      | 🟢 Low      | 5 min   | Performance  |
| 7     | God function in main.py                  | 🟡 Medium   | 1 hour  | Architecture |
| 8     | Mixin state contract undocumented        | 🟡 Medium   | 30 min  | Architecture |
| 9     | Dead code (TradesHistory, aliases)       | 🟢 Low      | 5 min   | Cleanup      |
| 10    | Config validation gap (leverage)         | 🟡 Medium   | 10 min  | Safety       |
| 11    | No watchdog for background tasks         | 🟡 Medium   | 30 min  | Reliability  |
| 12    | No per-exchange concurrency limit        | 🟡 Medium   | 20 min  | Scalability  |
| 13-18 | Frontend issues                          | 🟢 Low      | Various | Frontend     |
| 19-22 | Senior nitpicks                          | 🟢 Low      | Various | Polish       |

---

## Final Verdict

**For a solo-authored trading bot, this is strong work.** The trading logic (tier system, cherry-pick vs nutcracker vs pot mode, basis guard) shows deep understanding of the funding-rate arbitrage domain. The error recovery (orphan close, delta correction, fill verification) is more thorough than most institutional trading bots I've reviewed.

The three critical fixes (dict iteration, PnL dedup, prefix bypass) should be done **before the next overnight run**. Everything else can be addressed incrementally.

The frontend is clean and performant — the WebSocket reference-equality pattern in `App.tsx` is something I'd highlight as exemplary in a code review.

**Ship it, after fixing #1–#3.**
