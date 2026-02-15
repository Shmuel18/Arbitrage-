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
from typing import Optional

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
        print("\n⚠️  LIVE TRADING MODE — real money at risk!")
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
    all_symbol_sets = [set(a._exchange.markets.keys()) for a in mgr.all().values()]
    if len(all_symbol_sets) >= 2:
        # Union of all symbols, then keep only those on at least 2 exchanges
        all_symbols = set.union(*all_symbol_sets)
        symbol_counts = {s: sum(1 for ss in all_symbol_sets if s in ss) for s in all_symbols}
        common_symbols = sorted(s for s, c in symbol_counts.items() if c >= 2)
    else:
        common_symbols = sorted(all_symbol_sets[0]) if all_symbol_sets else []
    logger.info(f"Found {len(common_symbols)} tradeable symbols (on 2+ exchanges) across {len(verified)} exchanges")
    for adapter in mgr.all().values():
        # Only warm up symbols that exist on this specific exchange
        adapter_symbols = [s for s in common_symbols if s in adapter._exchange.markets]
        await adapter.warm_up_symbols(adapter_symbols)

    # ── Components ───────────────────────────────────────────────
    publisher = APIPublisher(redis)
    guard = RiskGuard(cfg, mgr, redis)
    controller = ExecutionController(cfg, mgr, redis, guard)
    scanner = Scanner(cfg, mgr, redis, publisher=publisher)

    await controller.start()
    await guard.start()

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
                
                # Publish active positions details
                positions_data = []
                for tid, trade in controller._active_trades.items():
                    positions_data.append({
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
                    })
                await publisher.publish_positions(positions_data)
                
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
    await asyncio.gather(scan_task, status_task, return_exceptions=True)

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
