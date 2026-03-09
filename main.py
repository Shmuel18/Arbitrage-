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
import sys

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


async def main() -> None:
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
    )
    await redis.connect()

    # ── Exchanges ────────────────────────────────────────────────
    mgr = ExchangeManager()
    for eid in cfg.enabled_exchanges:
        exc_cfg = cfg.exchanges.get(eid)
        if not exc_cfg:
            logger.warning(f"No config for exchange {eid}, skipping")
            continue
        exc_dict = exc_cfg.model_dump()
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

    # ── Components ───────────────────────────────────────────────
    publisher = APIPublisher(redis)
    guard = RiskGuard(cfg, mgr, redis)
    controller = ExecutionController(cfg, mgr, redis, guard, publisher=publisher)
    scanner = Scanner(cfg, mgr, redis, publisher=publisher)

    await controller.start()
    await guard.start()

    # ── Embedded API server (runs inside bot process — never dies separately) ──
    from api.main import app as api_app, manager as ws_manager
    from api.broadcast_service import BroadcastService
    from api.routes import positions as pos_route, trades as trades_route
    from api.routes import controls as ctrl_route, analytics as ana_route
    # Inject the bot's own Redis client into the API routes
    pos_route.set_redis_client(redis)
    trades_route.set_redis_client(redis)
    ctrl_route.set_redis_client(redis)
    ana_route.set_redis_client(redis)
    # Store reference so API module can use it
    import api.main as api_module
    api_module.redis_client = redis

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

    logger.info("Bot is running — press Ctrl+C to stop")
    await shutdown_event.wait()

    # ── Graceful shutdown ────────────────────────────────────────
    logger.info("Shutting down…")
    scanner.stop()
    scan_task.cancel()
    status_task.cancel()
    broadcast_task.cancel()
    uvicorn_server.should_exit = True
    await asyncio.gather(scan_task, status_task, broadcast_task, return_exceptions=True)
    # Give uvicorn a moment to close sockets
    await asyncio.sleep(0.5)
    api_task.cancel()
    await asyncio.gather(api_task, return_exceptions=True)

    # Close all open positions before exiting
    await controller.close_all_positions()
    await controller.stop()
    await guard.stop()
    await mgr.disconnect_all()
    await redis.disconnect()

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
