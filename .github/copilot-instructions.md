# Coding Standards — Trinity Arbitrage Bot

> These rules apply to ALL code written in this project.
> Copilot must follow them without exception. No shortcuts.

---

## 1. Python — General

### 1.1 Type Hints — Mandatory

```python
# ❌ NEVER
def calculate_spread(long_rate, short_rate, interval):
    ...

# ✅ ALWAYS
def calculate_spread(
    long_rate: Decimal,
    short_rate: Decimal,
    interval: int = 8,
) -> dict[str, Decimal]:
    ...
```

- Every function signature must have full type hints (args + return).
- Use `from __future__ import annotations` for forward references.
- Use `Optional[X]` or `X | None` explicitly — never leave `None` implicit.
- Use `TYPE_CHECKING` guard for import-only types to avoid circular imports.

### 1.2 Financial Math — Decimal Only

```python
# ❌ NEVER use float for money/rates
spread = 0.0001 * 100

# ✅ ALWAYS use Decimal
from decimal import Decimal
_HUNDRED = Decimal("100")  # module-level constant
spread = Decimal("0.0001") * _HUNDRED
```

- All funding rates, prices, quantities, fees, PnL → `Decimal`.
- Pre-define Decimal constants at module level: `_ZERO`, `_HUNDRED`, `_ONE`.
- Convert to `float` only at the boundary (JSON serialization, logging display).

### 1.3 Error Handling — No Silent Swallowing

```python
# ❌ NEVER
try:
    result = await risky_call()
except Exception:
    pass

# ✅ ALWAYS log + specify exception type
try:
    result = await risky_call()
except (ccxt.NetworkError, asyncio.TimeoutError) as e:
    logger.warning(f"Network issue in risky_call: {e}")
except Exception as e:
    logger.debug(f"Unexpected error in risky_call: {e}")
```

- Catch the **narrowest** exception type possible.
- Every `except` block must either log, re-raise, or have a comment explaining why it's silenced.
- `except Exception: pass` is **banned**.

### 1.4 Datetime — Timezone-Aware Only

```python
# ❌ BANNED (deprecated in 3.12+)
from datetime import datetime
now = datetime.utcnow()

# ✅ ALWAYS
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
```

- Every `datetime` in the system must be timezone-aware (UTC).
- When converting timestamps: `datetime.fromtimestamp(ts, tz=timezone.utc)`.

### 1.5 Logging — Guard Expensive Formatting

```python
# ❌ BAD — f-string evaluated even if DEBUG is disabled
logger.debug(f"Rates: {' | '.join(f'{k}={v:.8f}' for k, v in rates.items())}")

# ✅ GOOD — skip formatting entirely when not needed
if logger.isEnabledFor(logging.DEBUG):
    detail = " | ".join(f"{k}={v:.8f}" for k, v in rates.items())
    logger.debug(f"Rates: {detail}")
```

- Any `logger.debug()` with non-trivial f-string formatting must be guarded.
- `logger.info()` and above: guard only if the formatting is expensive (loops, joins).

### 1.6 Imports — Clean and Organized

```
# Order: stdlib → third-party → local
# Separate each group with a blank line

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import ccxt.pro as ccxtpro

from src.core.config import Config
from src.core.contracts import TradeMode, TradeRecord
```

- Never import inside a function/loop unless there's a circular import reason.
- Move `time`, `json`, etc. to module-level — not inside loops.
- `HTTPException`, model classes → module-level, not inside route handlers.

---

## 2. Python — Async Patterns

### 2.1 Parallel I/O — asyncio.gather

```python
# ❌ NEVER — sequential when independent
balance_a = await exchange_a.get_balance()
balance_b = await exchange_b.get_balance()
balance_c = await exchange_c.get_balance()

# ✅ ALWAYS — parallel when independent
balance_a, balance_b, balance_c = await asyncio.gather(
    exchange_a.get_balance(),
    exchange_b.get_balance(),
    exchange_c.get_balance(),
    return_exceptions=True,
)
```

- Independent I/O calls must use `asyncio.gather()`.
- Use `return_exceptions=True` when partial failure is acceptable.

### 2.2 Concurrency Control — Semaphores

```python
# ❌ NEVER — unbounded concurrent requests
await asyncio.gather(*[fetch(s) for s in symbols])

# ✅ ALWAYS — bounded with semaphore
semaphore = asyncio.Semaphore(10)
async def bounded_fetch(s):
    async with semaphore:
        return await fetch(s)
await asyncio.gather(*[bounded_fetch(s) for s in symbols])
```

- REST API calls → semaphore (10-20 concurrent).
- WebSocket connections → manage lifecycle explicitly.
- **Never bypass the semaphore** by calling the underlying method directly.

### 2.3 Background Tasks — Supervised

```python
# ❌ NEVER — fire-and-forget
asyncio.create_task(background_job())

# ✅ ALWAYS — supervised with error handling
task = asyncio.create_task(background_job(), name="background_job")
task.add_done_callback(_task_done_handler)

def _task_done_handler(t: asyncio.Task) -> None:
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        logger.error(f"Task {t.get_name()} failed: {exc}")
```

- Every `create_task()` must have a `name` and a done callback.
- Log exceptions from background tasks — never let them vanish silently.

### 2.4 Dict Iteration — Never Mutate During Iteration

```python
# ❌ CRASH — RuntimeError: dictionary changed size during iteration
for trade_id, trade in self._active_trades.items():
    if should_remove(trade):
        del self._active_trades[trade_id]

# ✅ SAFE — snapshot with list()
for trade_id, trade in list(self._active_trades.items()):
    if should_remove(trade):
        del self._active_trades[trade_id]
```

---

## 3. Python — Architecture

### 3.1 Abstraction Layers — Never Bypass

```python
# ❌ NEVER — accessing internal client directly
data = await redis_client._client.zrangebyscore(...)
await redis_client._client.publish(...)

# ✅ ALWAYS — use the abstraction method
data = await redis_client.zrangebyscore(...)
await redis_client.publish(...)
```

- If an abstraction (RedisClient, ExchangeAdapter) doesn't have the method you need → **add it to the abstraction**.
- Never access `._client`, `._exchange`, or other private attributes from outside the class.

### 3.2 Immutability — Protect Shared State

```python
# ❌ DANGEROUS — caller can mutate the registry
def all(self) -> dict[str, Adapter]:
    return self._adapters

# ✅ SAFE — read-only view
from types import MappingProxyType
def all(self) -> MappingProxyType:
    return MappingProxyType(self._adapters)
```

- Registries, configs, shared dicts → return `MappingProxyType` or copies.
- Use `frozenset` for immutable sets.

### 3.3 Protocol Classes — Type-Safe Interfaces

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ControllerProtocol(Protocol):
    _cfg: Config
    _redis: RedisClient
    async def close_trade(self, trade: TradeRecord) -> None: ...
```

- Use `Protocol` for mixin type safety and dependency injection.
- Mark with `@runtime_checkable` when `isinstance()` checks are needed.

### 3.4 Configuration — Never Hardcode

```python
# ❌ NEVER
if spread > 0.05:  # magic number

# ✅ ALWAYS — from config
if spread > self._cfg.trading_params.min_funding_spread:
```

- All thresholds, intervals, limits → config file.
- Document each config parameter with comments.

---

## 4. TypeScript / React

### 4.1 Shared Utilities — DRY

```typescript
// ❌ NEVER — duplicate formatter in 3 components
const formatCurrency = (n: number) => `$${n.toFixed(2)}`;

// ✅ ALWAYS — shared utility module
// utils/format.tsx
const currencyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
export const formatCurrency = (n: number) => currencyFmt.format(n);
```

- Formatters, badge components, helper functions → `utils/` folder.
- `Intl.NumberFormat` → cached at **module level**, not created per render.
- If the same component/logic appears in 2+ files → extract immediately.

### 4.2 Auto-Scroll — Respect User Position

```typescript
// ❌ BAD — hijacks user scroll
useEffect(() => {
  bottomRef.current?.scrollIntoView();
}, [messages]);

// ✅ GOOD — only auto-scroll if user is near bottom
const isNearBottom =
  container.scrollHeight - container.scrollTop - container.clientHeight < 60;
if (isNearBottom) {
  bottomRef.current?.scrollIntoView({ behavior: "smooth" });
}
```

### 4.3 Component Props — Typed Interfaces

```typescript
// ❌ NEVER
function Panel({ data, onUpdate }: any) { ... }

// ✅ ALWAYS
interface PanelProps {
  data: TradeData[];
  onUpdate: (id: string) => void;
}
function Panel({ data, onUpdate }: PanelProps) { ... }
```

### 4.4 WebSocket — Resilient Connection

- Auto-reconnect with exponential backoff.
- Show connection status indicator in the UI.
- Buffer messages during reconnection.

---

## 5. Testing

### 5.1 Test Structure

```python
class TestCalculator:
    """Group related tests in a class."""

    def test_positive_spread(self):
        """Test name describes the scenario, not the function."""
        result = calculate_spread(...)
        assert result["net_pct"] > 0

    def test_negative_spread_returns_zero(self):
        """Edge case: negative spread should floor to zero."""
        ...
```

- One test class per module/feature.
- Test name = scenario being tested.
- Each test has a docstring explaining what it verifies.

### 5.2 Mocking — Minimal and Precise

```python
# ❌ BAD — mocking everything
@patch("module.ClassA")
@patch("module.ClassB")
@patch("module.ClassC")
def test_something(mock_c, mock_b, mock_a): ...

# ✅ GOOD — mock only external boundaries
async def test_something():
    redis = AsyncMock(spec=RedisClient)
    redis.get.return_value = '{"key": "value"}'
    scanner = Scanner(config, exchange_mgr, redis)
    result = await scanner.scan_all()
```

- Mock at boundaries (Redis, HTTP, exchange APIs).
- Use `spec=` to catch API drift.
- Prefer `AsyncMock` for async methods.

### 5.3 Coverage Target

- Minimum: **60%** overall.
- Critical paths (calculator, sizer, risk guard): **90%+**.
- New code must include tests — no PR without tests for new logic.

---

## 6. Git & Code Organization

### 6.1 Commit Messages

```
<type>: <short description>

Types: fix, feat, refactor, test, docs, perf, chore
Examples:
  fix: prevent dict mutation crash in monitor loop
  feat: add zrange method to RedisClient abstraction
  perf: parallelize broadcast_updates with asyncio.gather
  refactor: extract StatusPublisher from main.py closure
```

### 6.2 File Size Limits

- Python files: **< 500 lines** preferred, **< 800** max.
- If a file exceeds 500 lines → consider splitting (mixins, modules).
- React components: **< 300 lines** — extract sub-components.

### 6.3 No Dead Code

- Remove commented-out code blocks.
- Remove unused imports (enforced by linter).
- Remove unused functions/variables.

---

## 7. Performance

### 7.1 Redis — Batch Operations

```python
# ❌ NEVER — N round-trips
for key in keys:
    value = await redis.get(key)

# ✅ ALWAYS — pipeline or single call
values = await redis.mget(keys)
# or use pipeline for mixed operations
```

### 7.2 Hot Path Optimization

- Scanner runs every 5s across hundreds of symbols — every ms counts.
- Cache instrument specs, mark prices, funding rates in memory.
- Use `set` for O(1) lookups instead of `list` scans.
- Pre-compute stable data (common symbols) with TTL-based refresh.

### 7.3 Memory — Avoid Leaks

- Sorted sets in Redis: trim with `zremrangebyscore` (keep last 24h).
- Lists in Redis: trim with `ltrim` after `lpush`.
- In-memory caches: bounded size or TTL eviction.

---

## 8. Security

- **Never** log API keys, secrets, or passwords.
- **Never** commit `.env` files or credentials.
- Use environment variables for all secrets.
- Validate all external input (API responses, WebSocket messages).

---

## 9. Trading Logic — Next Payment Only

### 9.1 Entry AND Hold — Same Window Rules

```python
# ❌ NEVER — staying in trade with next funding 7 hours away
if net_spread >= min_funding_spread:
    return True  # "rates are good" but payment is hours away

# ✅ ALWAYS — check that INCOME side fires within entry window
entry_window_min = tp.max_entry_window_minutes  # 60 min
long_imminent = long_is_income and long_mins <= entry_window_min
short_imminent = short_is_income and short_mins <= entry_window_min
if not (long_imminent or short_imminent):
    return False  # next income payment too far — EXIT
```

- The bot evaluates **only the next upcoming funding payment** — never future payments.
- Both at **entry** (scanner) AND **while holding** (exit logic), the decision must be based on the **imminent payment within `max_entry_window_minutes`**.
- After collecting a funding payment, the bot must not stay waiting hours for the next one.
- `_next_funding_qualifies()` must apply the **same entry-window logic** as the scanner:
  classify income/cost sides, check if income fires within the window, compute imminent net.
- `hold_max_wait_seconds` is a **backup safety net**, not the primary decision gate.

---

## 10. Anti-Patterns — Instant Red Flags

| Anti-Pattern                    | Fix                           |
| ------------------------------- | ----------------------------- |
| `except Exception: pass`        | Log or specify exception type |
| `datetime.utcnow()`             | `datetime.now(timezone.utc)`  |
| `redis._client.xxx()`           | Add method to `RedisClient`   |
| `float` for money               | `Decimal`                     |
| Sequential independent I/O      | `asyncio.gather()`            |
| Fire-and-forget `create_task`   | Add done callback             |
| Hardcoded threshold             | Move to config                |
| Duplicate component/function    | Extract to shared module      |
| `import X` inside loop          | Move to module level          |
| Dict mutation during iteration  | `list(dict.items())`          |
| Unbounded `asyncio.gather`      | Use `Semaphore`               |
| `Intl.NumberFormat()` in render | Cache at module level         |

---

_Last updated: 2026-03-05 — After 4 code review rounds (7.5 → 10/10)_
