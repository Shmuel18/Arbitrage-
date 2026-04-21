# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Trinity** is a delta-neutral funding rate arbitrage bot. It exploits funding rate differentials across crypto futures exchanges by holding a long on one exchange and a short on another — collecting funding payments while price moves cancel out.

## Commands

### Backend
```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (starts embedded FastAPI on port 8000)
# Windows: .\run.ps1
python main.py

# Run all tests with coverage
pytest

# Run a single test file
pytest tests/test_controller.py

# Run a single test by name
pytest tests/test_controller.py::TestClass::test_method
```

### Frontend
```bash
cd frontend
npm install
npm run dev        # Vite dev server on port 3000
npm run build      # Production build
```

### API only
```bash
pip install -r api/requirements.txt
uvicorn api.main:app --port 8000
```

## Architecture

### Data flow
```
Scanner (5s loop + WS hot-scan debounced 100ms)
  → ExecutionController (state machine: IDLE → VALIDATING → PENDING → ACTIVE → CLOSED)
  → RiskGuard (independent watchdog: 5s fast loop, 60s deep loop)
  → RedisClient (state persistence, pub/sub)
  → BroadcastService (WebSocket push to frontend)
  → React dashboard (http://localhost:3000)
```

### Key modules

| Path | Role |
|------|------|
| `main.py` | Entry point — wires all components, starts embedded FastAPI |
| `config.yaml` | All thresholds and risk params — never hardcode these |
| `src/core/contracts.py` | Frozen dataclasses: `Position`, `TradeRecord`, `OpportunityCandidate`, enums |
| `src/core/config.py` | Pydantic settings (YAML + env overlay) |
| `src/exchanges/adapter.py` | `ExchangeAdapter` + `ExchangeManager` wrapping ccxt.pro |
| `src/discovery/scanner.py` | Opportunity detection (full scan + hot-scan path) |
| `src/execution/controller.py` | State machine with 7 mixins (entry, monitor, close, exit_logic, util, etc.) |
| `src/risk/guard.py` | Independent watchdog — delta check, margin check, orphan detection, panic close |
| `src/storage/redis_client.py` | Async Redis wrapper — all state persistence goes through this |
| `api/main.py` | FastAPI server (REST + WebSocket `/ws`) |
| `api/broadcast_service.py` | Subscribes to Redis keyspace, pushes live updates to WS clients |

### Execution controller mixins
The controller is composed via 7 mixins to stay under 500-line limits. The Protocol class (`ControllerProtocol`) enforces type safety across mixin boundaries. Adding methods to the controller means adding them to the appropriate mixin or creating a new one.

### Frontend state
The frontend uses a reducer pattern (`useMarketReducer`) fed by two hooks: `useMarketData` (REST polling) and `useWsFeed` (WebSocket). The WebSocket auto-reconnects with exponential backoff.

## Coding Standards

These are enforced — treat them as hard rules:

### Financial math
- All prices, rates, quantities, PnL → `Decimal`. Never `float`.
- Pre-define constants at module level: `_ZERO = Decimal("0")`, `_HUNDRED = Decimal("100")`.
- Convert to `float` only at JSON/display boundaries.

### Async patterns
- Independent I/O → `asyncio.gather()`, always.
- REST calls → bounded by `asyncio.Semaphore(10)`. Never bypass the semaphore.
- Every `create_task()` must have a `name=` and a `_task_done_handler` done callback.

### Error handling
- `except Exception: pass` is banned. Every except must log, re-raise, or have a comment.
- Catch the narrowest exception type possible.

### Datetime
- `datetime.utcnow()` is banned. Use `datetime.now(timezone.utc)`.

### Abstraction layers
- Never access `._client`, `._exchange`, or other private attributes from outside the class.
- If `RedisClient` or `ExchangeAdapter` is missing a method you need → add it to the abstraction.

### Dict mutation safety
- Always snapshot before mutating: `for k, v in list(d.items()):`.

### Logging
- Guard expensive `debug()` formatting: `if logger.isEnabledFor(logging.DEBUG):`.

### TypeScript
- `Intl.NumberFormat` instances are cached at module level, never created per render.
- All component props typed with interfaces, never `any`.

## Trading Logic — Critical Rule

The bot evaluates **only the next upcoming funding payment**. Both at entry (scanner) and while holding (exit logic), the income side must fire within `max_entry_window_minutes` (default 60 min). Do not stay in a trade waiting hours for the next funding cycle — `_next_funding_qualifies()` and the scanner must apply the same window check.

## Anti-Patterns (instant red flags)

| Anti-pattern | Fix |
|---|---|
| `except Exception: pass` | Log or specify exception type |
| `datetime.utcnow()` | `datetime.now(timezone.utc)` |
| `redis._client.xxx()` | Add method to `RedisClient` |
| `float` for money/rates | `Decimal` |
| Sequential independent I/O | `asyncio.gather()` |
| Fire-and-forget `create_task` | Add done callback |
| Hardcoded threshold | Move to `config.yaml` |
| Duplicate component/function | Extract to `utils/` |
| Dict mutation during iteration | `list(dict.items())` |
| Unbounded `asyncio.gather` | Use `Semaphore` |
| `Intl.NumberFormat()` in render | Cache at module level |

## File Size Limits
- Python: < 500 lines preferred, < 800 max. Over 500 → split into mixins/modules.
- React components: < 300 lines. Over 300 → extract sub-components.

## Commit Message Format
```
<type>: <short description>
Types: fix, feat, refactor, test, docs, perf, chore
```

## Tests
- Target: 55% overall coverage minimum (`pytest.ini`).
- Critical paths (calculator, sizer, risk guard): aim for 90%+.
- Mock at boundaries only (Redis, exchange APIs). Use `spec=` to catch API drift.
- Async tests use `asyncio_mode = auto` (no manual `@pytest.mark.asyncio` needed).
