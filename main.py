"""
Main entry point — wire everything together, run the bot.

Safety features:
  • signal handler sets event (no create_task in handler)
  • graceful shutdown closes all open positions
  • live-mode confirmation prompt
"""

from __future__ import annotations

import asyncio
import signal
import socket
import sys

# Use uvloop on Linux/Mac for faster asyncio (2-4x I/O throughput).
# No-op on Windows or if uvloop is not installed.
try:
    import uvloop  # type: ignore[import-not-found]

    uvloop.install()
except ImportError:
    pass

import uvicorn

from src.core.config import init_config
from src.core.logging import get_logger
from src.core.status_publisher import StatusPublisher
from src.discovery.scanner import Scanner
from src.exchanges.adapter import ExchangeManager
from src.execution.controller import ExecutionController
from src.risk.guard import RiskGuard
from src.storage.redis_client import RedisClient
from src.api.publisher import APIPublisher

logger = get_logger("main")

_WS_NOISE_PHRASES = (
    "ping-pong keepalive",
    "closing code 1006",
    "closing code 1000",
    "closing code 1001",
    "cannot write to closing transport",
    "the specified network name is no longer available",
    # Bitget (and similar) reject subscriptions for delisted symbols with this phrase.
    # Our _watch_price_tickers_loop already catches and drops them, but ccxt's internal
    # subscription futures also raise the error and Python prints it as
    # "Future exception was never retrieved".  Suppress those here.
    "doesn't exist",
    "does not exist",
    "precision:null",          # bitget delisted-symbol error variant
    "connection was forcibly closed",  # Windows WinError 10054
)


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Suppress noisy 'Future exception was never retrieved' from ccxt WS internals.

    ccxt spawns internal asyncio Futures for ping-pong keepalive. When the
    WebSocket disconnects those futures raise RequestTimeout / NetworkError but
    nothing awaits them, so Python would normally spam the console. We demote
    those to DEBUG and let everything else go to the default handler.
    """
    exc = context.get("exception")
    msg = context.get("message", "")

    _msg_lower = msg.lower()
    if exc is not None and (
        "future exception was never retrieved" in _msg_lower
        or "task exception was never retrieved" in _msg_lower
        or "exception in callback" in _msg_lower
        or "accept failed on a socket" in _msg_lower
    ):
        exc_str = str(exc).lower()
        if any(phrase in exc_str for phrase in _WS_NOISE_PHRASES):
            if logger.isEnabledFor(10):  # DEBUG
                logger.debug(f"[WS noise suppressed] {exc}")
            return
        # Also suppress ccxt BadRequest exceptions (delisted symbols, invalid
        # subscriptions, etc.) — they are always transient WS noise.
        exc_cls = type(exc).__name__.lower()
        if exc_cls in ("badrequest", "networkerror", "requesttimeout"):
            if logger.isEnabledFor(10):  # DEBUG
                logger.debug(f"[WS noise suppressed by class] {exc_cls}: {exc}")
            return

    loop.default_exception_handler(context)


async def main() -> None:
    # ── Suppress ccxt WS ping-pong noise ─────────────────────────
    asyncio.get_running_loop().set_exception_handler(_asyncio_exception_handler)

    # ── Config ───────────────────────────────────────────────────
    cfg = init_config()
    cfg.validate_safety()

    logger.info(f"Trinity v{cfg.version} starting",
                extra={"action": "startup",
                       "data": {"env": cfg.environment,
                                "paper": cfg.paper_trading,
                                "dry_run": cfg.dry_run,
                                "exchanges": cfg.enabled_exchanges,
                                "symbols": "all"}})

    # Live-mode gate
    if not cfg.paper_trading and not cfg.dry_run:
        print("\n[WARNING] LIVE TRADING MODE --- real money at risk!")
        print(f"   Exchanges : {cfg.enabled_exchanges}")
        print(f"   Position size: {cfg.risk_limits.position_size_pct} (70% of min balance × leverage)")
        answer = input("   Type YES to continue: ")
        if answer.strip() != "YES":
            print("Aborted.")
            return

    # ── Redis ────────────────────────────────────────────────────
    redis = RedisClient(
        url=cfg.redis.url,
        prefix=cfg.redis.key_prefix,
        password=cfg.redis.password_plaintext,
        tls=cfg.redis.tls,
    )
    await redis.connect()

    # ── Exchanges ────────────────────────────────────────────────
    mgr = ExchangeManager()
    for eid in cfg.enabled_exchanges:
        exc_cfg = cfg.exchanges.get(eid)
        if not exc_cfg:
            logger.warning(f"No config for exchange {eid}, skipping")
            continue
        exc_dict = exc_cfg.to_adapter_dict()  # unwraps SecretStr at boundary
        exc_dict["max_sane_funding_rate"] = float(cfg.trading_params.max_sane_funding_rate)
        mgr.register(eid, exc_dict)

    await mgr.connect_all()

    # Verify credentials — remove exchanges with bad keys
    verified = await mgr.verify_all()
    cfg.enabled_exchanges = verified
    if len(verified) < 2:
        logger.error(f"Only {len(verified)} exchange(s) verified — need at least 2. Aborting.")
        await mgr.disconnect_all()
        await redis.disconnect()
        return
    logger.info(f"Verified {len(verified)} exchanges: {verified}",
                extra={"action": "exchanges_verified", "data": {"exchanges": verified}})

    # Warm up instrument specs for ALL symbols available on 2+ exchanges
    all_symbol_sets = [set(a.symbols) for a in mgr.all().values()]
    if len(all_symbol_sets) >= 2:
        # Union of all symbols, then keep only those on at least 2 exchanges
        all_symbols = set.union(*all_symbol_sets)
        symbol_counts = {s: sum(1 for ss in all_symbol_sets if s in ss) for s in all_symbols}
        common_symbols = sorted(s for s, c in symbol_counts.items() if c >= 2)
    else:
        common_symbols = sorted(all_symbol_sets[0]) if all_symbol_sets else []
    logger.info(
        f"Found {len(common_symbols)} tradeable symbols (on 2+ exchanges) across {len(verified)} exchanges",
        extra={"action": "symbol_summary", "data": {"symbols": len(common_symbols)}},
    )
    market_counts = {eid: len(a.symbols) for eid, a in mgr.all().items()}
    logger.info(
        f"Market counts by exchange: {market_counts}",
        extra={"action": "market_counts", "data": market_counts},
    )
    for adapter in mgr.all().values():
        # Only warm up symbols that exist on this specific exchange
        adapter_symbols = [s for s in common_symbols if s in adapter.markets]
        await adapter.warm_up_symbols(adapter_symbols)

    # Batch-fetch ALL funding rates (one API call per exchange → instant cache)
    logger.info("Fetching funding rates (batch)...", extra={"action": "funding_batch_start"})
    for adapter in mgr.all().values():
        adapter_symbols = [s for s in common_symbols if s in adapter.symbols]
        await adapter.warm_up_funding_rates(adapter_symbols)
    logger.info("Funding rate cache ready", extra={"action": "funding_batch_done"})

    # Apply trading settings (cross margin, leverage, position mode) for ALL symbols
    # at startup — eliminates 3-7s per-symbol latency on first trade.
    logger.info("Applying trading settings (cross margin) on all exchanges...",
                extra={"action": "settings_warm_start"})
    settings_tasks = []
    for adapter in mgr.all().values():
        adapter_symbols = [s for s in common_symbols if s in adapter.symbols]
        settings_tasks.append(adapter.warm_up_trading_settings(adapter_symbols))
    await asyncio.gather(*settings_tasks)  # all exchanges in parallel
    logger.info("Trading settings applied on all exchanges",
                extra={"action": "settings_warm_done"})

    # ── Components ───────────────────────────────────────────────
    # Telegram notifier — None when disabled; publish_alert() no-ops Telegram.
    telegram = None
    if cfg.telegram.enabled:
        from src.notifications.telegram_notifier import TelegramNotifier
        telegram = TelegramNotifier(cfg.telegram)
        # Send startup ping — surfaces misconfig at boot, not at first trade.
        await telegram.self_test()

    publisher = APIPublisher(redis, telegram=telegram)
    guard = RiskGuard(cfg, mgr, redis)
    controller = ExecutionController(cfg, mgr, redis, guard, publisher=publisher)
    scanner = Scanner(cfg, mgr, redis, publisher=publisher)

    await controller.start()
    await guard.start()

    # ── Embedded API server (runs inside bot process — never dies separately) ──
    from api.main import app as api_app, manager as ws_manager
    from api.broadcast_service import BroadcastService
    # Inject bot Redis client into FastAPI application state for route DI.
    api_app.state.redis_client = redis

    # Probe port 8000 before attempting to bind.  If an external API server
    # (e.g. the VS Code "Run API Server" task) is already listening, skip the
    # embedded server rather than crashing with OSError / SystemExit(1).
    def _port_in_use(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            try:
                _s.bind((host, port))
                return False
            except OSError:
                return True

    uvicorn_server = None
    api_task = None
    broadcast_task = None

    if _port_in_use("0.0.0.0", 8000):
        logger.warning(
            "Port 8000 already in use — skipping embedded API server. "
            "An external API server is likely running.",
            extra={"action": "api_skipped"},
        )
    else:
        uvicorn_config = uvicorn.Config(
            api_app, host="0.0.0.0", port=8000,
            log_level="warning",   # suppress noisy access logs
            lifespan="off",        # we manage lifecycle ourselves (Redis already connected)
            ws_ping_interval=60,   # ping every 60s (default 20) — tolerates event-loop congestion
            ws_ping_timeout=60,    # wait 60s for pong (default 20) — avoids premature disconnects
        )
        uvicorn_server = uvicorn.Server(uvicorn_config)

        api_task = asyncio.create_task(uvicorn_server.serve(), name="api-server")
        # Start the WebSocket broadcast loop (extracted to BroadcastService)
        broadcast_svc = BroadcastService(ws_manager, redis)
        broadcast_task = asyncio.create_task(
            broadcast_svc.run_forever(), name="ws-broadcast",
        )

        def _broadcast_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(f"Task {t.get_name()} failed: {exc}")

        broadcast_task.add_done_callback(_broadcast_done)
        logger.info("Embedded API server started on port 8000",
                    extra={"action": "api_started"})

    # ── Shutdown signal ──────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _on_signal() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: shutdown_event.set())

    # ── Run scanner in background ────────────────────────────────
    scan_task = asyncio.create_task(
        scanner.start(controller.handle_opportunity), name="scanner",
    )
    
    # ── Run status publisher in background ─────────────────────────
    status_pub = StatusPublisher(cfg, mgr, controller, redis, publisher, shutdown_event)
    status_task = asyncio.create_task(status_pub.run(), name="status_publisher")

    # ── Daily summary (Telegram) ─────────────────────────────────
    summary_task = None
    if cfg.telegram.enabled and cfg.telegram.notify_daily_summary:
        from src.notifications.daily_summary import daily_summary_loop
        summary_task = asyncio.create_task(
            daily_summary_loop(publisher, redis, cfg.telegram, shutdown_event),
            name="daily_summary",
        )

    # ── Telegram command loop (inbound /start /status /menu) ────
    bot_cmd_task = None
    if cfg.telegram.enabled:
        from src.notifications.bot_commands import bot_commands_loop
        bot_cmd_task = asyncio.create_task(
            bot_commands_loop(cfg.telegram, redis, shutdown_event,
                              mini_app_url=cfg.telegram.mini_app_url),
            name="telegram_commands",
        )

    logger.info("Bot is running — press Ctrl+C to stop")
    await shutdown_event.wait()

    # ── Graceful shutdown ────────────────────────────────────────
    logger.info("Shutting down…")
    scanner.stop()
    scan_task.cancel()
    status_task.cancel()
    if summary_task is not None:
        summary_task.cancel()
    if bot_cmd_task is not None:
        bot_cmd_task.cancel()
    if broadcast_task is not None:
        broadcast_task.cancel()
    if uvicorn_server is not None:
        uvicorn_server.should_exit = True
    tasks_to_gather = [scan_task, status_task]
    if broadcast_task is not None:
        tasks_to_gather.append(broadcast_task)
    await asyncio.gather(*tasks_to_gather, return_exceptions=True)
    # Give uvicorn a moment to close sockets
    await asyncio.sleep(0.5)
    if api_task is not None:
        api_task.cancel()
        await asyncio.gather(api_task, return_exceptions=True)

    # Close all open positions before exiting
    await controller.close_all_positions()
    await controller.stop()
    await guard.stop()
    await mgr.disconnect_all()
    await redis.disconnect()
    if telegram is not None:
        await telegram.close()

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
