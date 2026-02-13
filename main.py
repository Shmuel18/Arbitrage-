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
from decimal import Decimal
from typing import Optional

from src.core.config import init_config
from src.core.logging import get_logger
from src.discovery.scanner import DiscoveryScanner as Scanner
from src.exchanges.adapter import ExchangeManager
from src.execution.controller import ExecutionController
from src.risk.guard import RiskGuard
from src.storage.redis_client import RedisClient

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
                                "symbols": len(cfg.watchlist)}})

    # Live-mode gate
    if not cfg.paper_trading and not cfg.dry_run:
        print("\n⚠️  LIVE TRADING MODE — real money at risk!")
        print(f"   Exchanges : {cfg.enabled_exchanges}")
        print(f"   Max margin: {cfg.risk_limits.max_margin_usage}")
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

    # Remove exchanges that failed to connect (_exchange is None)
    for eid in list(mgr.all().keys()):
        adapter = mgr.get(eid)
        if adapter._exchange is None:
            logger.warning(f"Removing {eid} — connection failed")
            cfg.enabled_exchanges = [e for e in cfg.enabled_exchanges if e != eid]
            try:
                await adapter.disconnect()
            except Exception:
                pass
            mgr.unregister(eid)

    # Verify credentials — remove exchanges with bad keys
    failed = await mgr.verify_all()
    for bad_eid in failed:
        logger.warning(f"Removing {bad_eid} — credentials failed")
        cfg.enabled_exchanges = [e for e in cfg.enabled_exchanges if e != bad_eid]
        try:
            await mgr.get(bad_eid).disconnect()
        except Exception:
            pass
        mgr.unregister(bad_eid)
    verified = cfg.enabled_exchanges
    if len(verified) < 2:
        logger.error(f"Only {len(verified)} exchange(s) verified — need at least 2. Aborting.")
        await mgr.disconnect_all()
        await redis.disconnect()
        return
    logger.info(f"Verified {len(verified)} exchanges: {verified}",
                extra={"action": "exchanges_verified", "data": {"exchanges": verified}})

    # Log portfolio size (USDT) per exchange
    total_usdt = Decimal("0")
    for eid, adapter in mgr.all().items():
        try:
            bal = await adapter.get_balance()
            total_usdt += bal["total"]
            logger.info(
                f"{eid} balance — total={bal['total']} free={bal['free']} used={bal['used']}",
                extra={"action": "balance", "data": {"exchange": eid, "total": str(bal["total"]), "free": str(bal["free"]), "used": str(bal["used"])}},
            )
        except Exception as exc:
            logger.warning(f"Failed to fetch balance for {eid}: {exc}")
    logger.info(
        f"Total portfolio USDT (sum of exchanges): {total_usdt}",
        extra={"action": "balance_total", "data": {"total_usdt": str(total_usdt)}},
    )

    # Warm up instrument specs
    for adapter in mgr.all().values():
        await adapter.warm_up_symbols(cfg.watchlist)

    # ── Components ───────────────────────────────────────────────
    guard = RiskGuard(mgr, redis)
    controller = ExecutionController(mgr, redis)
    scanner = Scanner(mgr, controller)

    await controller.start_exit_monitor()
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
    await scanner.start()

    logger.info("Bot is running — press Ctrl+C to stop")
    await shutdown_event.wait()

    # ── Graceful shutdown ────────────────────────────────────────
    logger.info("Shutting down…")
    await scanner.stop()

    # Close all open positions before exiting
    await controller.close_all_positions()
    await controller.stop_exit_monitor()
    await guard.stop()
    await mgr.disconnect_all()
    await redis.disconnect()

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
