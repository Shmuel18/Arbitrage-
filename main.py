"""
Main entry point — wire everything together, run the bot.

Safety features:
  • signal handler sets event (no create_task in handler)
  • graceful shutdown closes all open positions
  • live-mode confirmation prompt
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn

from src.core.config import init_config
from src.core.logging import get_logger
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
        mgr.register(eid, exc_cfg.model_dump())

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
    all_symbol_sets = [set(a._exchange.symbols) for a in mgr.all().values()]
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
    market_counts = {eid: len(a._exchange.symbols) for eid, a in mgr.all().items()}
    logger.info(
        f"Market counts by exchange: {market_counts}",
        extra={"action": "market_counts", "data": market_counts},
    )
    for adapter in mgr.all().values():
        # Only warm up symbols that exist on this specific exchange
        adapter_symbols = [s for s in common_symbols if s in adapter._exchange.markets]
        await adapter.warm_up_symbols(adapter_symbols)

    # Batch-fetch ALL funding rates (one API call per exchange → instant cache)
    logger.info("Fetching funding rates (batch)...", extra={"action": "funding_batch_start"})
    for adapter in mgr.all().values():
        adapter_symbols = [s for s in common_symbols if s in adapter._exchange.symbols]
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
    # Inject the bot's own Redis client into the API routes
    from api.routes import positions as pos_route, trades as trades_route
    from api.routes import controls as ctrl_route, analytics as ana_route
    pos_route.set_redis_client(redis)
    trades_route.set_redis_client(redis)
    ctrl_route.set_redis_client(redis)
    ana_route.set_redis_client(redis)
    # Store reference so broadcast_updates() can use it
    import api.main as api_module
    api_module.redis_client = redis

    uvicorn_config = uvicorn.Config(
        api_app, host="0.0.0.0", port=8000,
        log_level="warning",   # suppress noisy access logs
        lifespan="off",        # we manage lifecycle ourselves (Redis already connected)
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    api_task = asyncio.create_task(uvicorn_server.serve(), name="api-server")
    # Start the WebSocket broadcast loop (normally started by lifespan)
    from api.main import broadcast_updates
    broadcast_task = asyncio.create_task(broadcast_updates(), name="ws-broadcast")
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
    async def publish_status_loop():
        """Publish bot status + balances + summary to Redis every 5 seconds"""
        while not shutdown_event.is_set():
            try:
                # Get real active positions count
                active_count = len(controller._active_trades)
                
                # Publish bot status
                await publisher.publish_status(
                    running=True,
                    exchanges=cfg.enabled_exchanges,
                    positions_count=active_count,
                )
                
                # Fetch and publish real balances
                balances = {}
                for eid in cfg.enabled_exchanges:
                    adapter = mgr.get(eid)
                    if adapter:
                        try:
                            bal = await adapter.get_balance()
                            total_val = bal.get("total")
                            if isinstance(total_val, dict):
                                total_val = total_val.get("USDT")
                            if total_val is None:
                                total_val = bal.get("free", 0)
                            balances[eid] = float(total_val or 0)
                        except Exception:
                            balances[eid] = 0.0
                
                await publisher.publish_balances(balances)
                await publisher.publish_summary(balances, active_count)
                
                # Publish active positions details (with live spread)
                from src.discovery.calculator import calculate_funding_spread
                positions_data = []
                for tid, trade in controller._active_trades.items():
                    pos_entry = {
                        "id": trade.trade_id,
                        "symbol": trade.symbol,
                        "long_exchange": trade.long_exchange,
                        "short_exchange": trade.short_exchange,
                        "long_qty": str(trade.long_qty),
                        "short_qty": str(trade.short_qty),
                        "entry_edge_pct": str(trade.entry_edge_pct),
                        "long_funding_rate": str(trade.long_funding_rate) if trade.long_funding_rate is not None else None,
                        "short_funding_rate": str(trade.short_funding_rate) if trade.short_funding_rate is not None else None,
                        "mode": trade.mode,
                        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                        "state": trade.state.value,
                        "immediate_spread_pct": None,
                        "current_spread_pct": None,
                        "current_long_rate": None,
                        "current_short_rate": None,
                    }
                    # Use cached funding rates to compute current spread (no REST)
                    try:
                        long_ad = mgr.get(trade.long_exchange)
                        short_ad = mgr.get(trade.short_exchange)
                        live_long = long_ad.get_funding_rate_cached(trade.symbol)
                        live_short = short_ad.get_funding_rate_cached(trade.symbol)
                        if not live_long or not live_short:
                            raise ValueError("no cached rate")
                        spread_info = calculate_funding_spread(
                            live_long["rate"], live_short["rate"],
                            long_interval_hours=live_long.get("interval_hours", 8),
                            short_interval_hours=live_short.get("interval_hours", 8),
                        )
                        pos_entry["immediate_spread_pct"] = str(spread_info["immediate_spread_pct"])
                        pos_entry["current_spread_pct"] = str(spread_info["funding_spread_pct"])
                        pos_entry["current_long_rate"] = str(live_long["rate"])
                        pos_entry["current_short_rate"] = str(live_short["rate"])
                    except Exception as fr_err:
                        logger.debug(f"Live spread fetch failed for {trade.symbol}: {fr_err}")
                    positions_data.append(pos_entry)
                await publisher.publish_positions(positions_data)

                # ── Compute & publish running PnL (unrealized + realized) ──
                unrealized_pnl = 0.0
                for tid, trade in controller._active_trades.items():
                    try:
                        la = mgr.get(trade.long_exchange)
                        sa = mgr.get(trade.short_exchange)
                        long_positions = await la.get_positions(trade.symbol)
                        short_positions = await sa.get_positions(trade.symbol)
                        for pos in long_positions:
                            unrealized_pnl += float(pos.unrealized_pnl)
                        for pos in short_positions:
                            unrealized_pnl += float(pos.unrealized_pnl)
                    except Exception:
                        pass

                # Read realized PnL from closed trades
                realized_pnl = 0.0
                try:
                    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
                    closed_pnl = await redis._client.zrangebyscore(
                        "trinity:pnl:timeseries", cutoff, float('inf'), withscores=True
                    )
                    if closed_pnl:
                        for i in range(0, len(closed_pnl), 2):
                            if i + 1 < len(closed_pnl):
                                realized_pnl += float(closed_pnl[i])
                except Exception:
                    pass

                running_pnl = realized_pnl + unrealized_pnl
                ts_now = datetime.now(timezone.utc).timestamp()

                # Write running PnL snapshot for chart (every cycle = ~5s)
                try:
                    pnl_snapshot = json.dumps({"running": running_pnl, "unrealized": unrealized_pnl, "realized": realized_pnl})
                    await redis._client.zadd(
                        "trinity:pnl:running",
                        {pnl_snapshot: ts_now},
                    )
                    # Trim old entries (keep last 24h)
                    cutoff_trim = ts_now - 86400
                    await redis._client.zremrangebyscore("trinity:pnl:running", 0, cutoff_trim)
                except Exception:
                    pass

                # Publish PnL data for frontend (via Redis key for WebSocket + HTTP)
                try:
                    # Build data points from running PnL snapshots
                    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
                    running_data = await redis._client.zrangebyscore(
                        "trinity:pnl:running", cutoff_24h, float('inf'), withscores=True
                    )
                    data_points = []
                    if running_data:
                        for i in range(0, len(running_data), 2):
                            if i + 1 < len(running_data):
                                try:
                                    point = json.loads(running_data[i])
                                    data_points.append({
                                        "pnl": point.get("running", 0),
                                        "cumulative_pnl": point.get("running", 0),
                                        "unrealized": point.get("unrealized", 0),
                                        "realized": point.get("realized", 0),
                                        "timestamp": float(running_data[i + 1]),
                                    })
                                except Exception:
                                    pass
                    pnl_payload = {
                        "data_points": data_points,
                        "total_pnl": running_pnl,
                        "unrealized_pnl": unrealized_pnl,
                        "realized_pnl": realized_pnl,
                        "count": len(data_points),
                    }
                    await redis.set("trinity:pnl:latest", json.dumps(pnl_payload))
                except Exception as pnl_err:
                    logger.debug(f"PnL publish error: {pnl_err}")
                
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error publishing status: {e}")
                await asyncio.sleep(1)
    
    status_task = asyncio.create_task(publish_status_loop(), name="status_publisher")

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
