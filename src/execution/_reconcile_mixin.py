"""
Execution controller mixin — per-trade portfolio reconciliation.

Captures pre-entry and post-close balance snapshots across all enabled
exchanges, computes per-exchange deltas, and persists an audit record.
The drift between (sum of deltas) and (bot-computed expected_pnl)
flags scenarios where the internal PnL view diverges from exchange
truth — e.g. the LAB phantom-PnL incident from May 3 2026.

Do NOT import this module directly; use ExecutionController from controller.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.core.logging import get_logger
from src.core.reconciliation import (
    BalanceSnapshot,
    ReconciliationRecord,
    compute_deltas,
    split_untouched_drift,
)

logger = get_logger("execution")


_BALANCE_TIMEOUT_SEC = 5.0
_BALANCE_CONCURRENCY = 4


class _ReconcileMixin:
    """Snapshot, build, and persist per-trade reconciliation records."""

    async def _snapshot_balances(self, *, use_cached: bool = False) -> BalanceSnapshot:
        """Fetch totals from every enabled exchange in parallel.

        Each fetch is bounded by ``_BALANCE_TIMEOUT_SEC`` and the concurrency
        is capped at ``_BALANCE_CONCURRENCY``. Failed/timed-out exchanges go
        into ``failures`` and are excluded from delta math downstream.

        ``use_cached=True`` allows the entry path to use the 3-second cache
        and avoid adding REST latency. The post-close path always uses fresh
        data so deltas reflect actual settled balances.
        """
        adapters: List[Tuple[str, Any]] = []
        for exchange_id in self._cfg.enabled_exchanges:
            adapter = self._exchanges.get(exchange_id)
            if adapter is not None:
                adapters.append((exchange_id, adapter))

        if not adapters:
            return BalanceSnapshot(
                captured_at=datetime.now(timezone.utc),
                balances={},
                failures=(),
            )

        sem = asyncio.Semaphore(_BALANCE_CONCURRENCY)

        async def _one(adapter: Any) -> Dict[str, Any]:
            async with sem:
                if use_cached and hasattr(adapter, "get_balance_cached"):
                    return await adapter.get_balance_cached()
                return await adapter.get_balance()

        async def _bounded(adapter: Any) -> Dict[str, Any]:
            return await asyncio.wait_for(_one(adapter), timeout=_BALANCE_TIMEOUT_SEC)

        results = await asyncio.gather(
            *[_bounded(a) for _, a in adapters],
            return_exceptions=True,
        )

        balances: Dict[str, Decimal] = {}
        failures: List[str] = []
        for (exchange_id, _), result in zip(adapters, results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"Balance snapshot failed for {exchange_id}: {result}",
                    extra={"exchange": exchange_id, "action": "balance_snapshot_fail"},
                )
                failures.append(exchange_id)
                continue
            balances[exchange_id] = self._extract_total_equity(result)

        return BalanceSnapshot(
            captured_at=datetime.now(timezone.utc),
            balances=balances,
            failures=tuple(failures),
        )

    @staticmethod
    def _extract_total_equity(balance: Dict[str, Any]) -> Decimal:
        """Pull a single Decimal equity figure from an adapter's balance dict.

        Adapter returns ``{"total", "free", "used"}`` already as Decimals.
        Falls back to ``free + used`` when ``total`` is missing or zero —
        matches the convention used in ``_journal_balance_snapshot``.
        """
        total = balance.get("total")
        if total is not None:
            if not isinstance(total, Decimal):
                total = Decimal(str(total))
            if total > Decimal("0"):
                return total
        free = balance.get("free") or Decimal("0")
        used = balance.get("used") or Decimal("0")
        if not isinstance(free, Decimal):
            free = Decimal(str(free))
        if not isinstance(used, Decimal):
            used = Decimal(str(used))
        return free + used

    def _build_reconciliation_record(
        self,
        trade,
        pre: Optional[BalanceSnapshot],
        post: BalanceSnapshot,
        expected_pnl: Decimal,
    ) -> ReconciliationRecord:
        """Pure-Decimal record builder. No I/O — safe to unit-test directly."""
        deltas = compute_deltas(pre, post)
        net_delta = sum(deltas.values(), Decimal("0"))
        # When pre is missing the drift is unverifiable — surface NaN-equivalent
        # by setting drift=0 and partial=True so consumers can mask it.
        drift = (net_delta - expected_pnl) if pre is not None else Decimal("0")
        untouched = split_untouched_drift(
            deltas, (trade.long_exchange, trade.short_exchange),
        )
        return ReconciliationRecord(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            long_exchange=trade.long_exchange,
            short_exchange=trade.short_exchange,
            pre=pre,
            post=post,
            deltas=deltas,
            net_delta=net_delta,
            expected_pnl=expected_pnl,
            drift=drift,
            untouched_drift=untouched,
            # Pair flatness verification lands in PR2 — for now assume the
            # existing post-close dust check has guaranteed it.
            pair_flat=True,
            global_flat=True,
            flatness_failures=(),
            partial=(pre is None),
        )

    async def _persist_reconciliation(self, rec: ReconciliationRecord) -> None:
        """Write to Redis (7d TTL + capped recent list) and the trade journal."""
        payload = rec.to_dict()
        try:
            await self._redis.set_reconciliation(rec.trade_id, payload)
        except Exception as exc:
            logger.warning(
                f"[{rec.symbol}] reconciliation Redis write failed: {exc}",
                extra={"trade_id": rec.trade_id, "action": "reconcile_redis_fail"},
            )
        try:
            self._journal.event(
                "reconciliation",
                trade_id=rec.trade_id,
                symbol=rec.symbol,
                net_delta=str(rec.net_delta),
                expected_pnl=str(rec.expected_pnl),
                drift=str(rec.drift),
                untouched_exchanges=list(rec.untouched_drift.keys()),
                partial=rec.partial,
            )
        except Exception as exc:
            logger.debug(
                f"[{rec.symbol}] reconciliation journal write failed: {exc}",
            )

    async def _capture_pre_snapshot(self, trade_id: str) -> None:
        """Best-effort pre-entry snapshot. Stores into _pending_pre_snapshots.

        Called from the entry path. Failures are logged but never raised —
        a missing pre-snapshot just sets ``partial=True`` on the record.
        """
        try:
            snap = await self._snapshot_balances(use_cached=True)
            self._pending_pre_snapshots[trade_id] = snap
        except Exception as exc:
            logger.warning(
                f"Pre-entry snapshot failed for trade {trade_id}: {exc}",
                extra={"trade_id": trade_id, "action": "pre_snapshot_fail"},
            )

    async def _record_reconciliation(
        self, trade, expected_pnl: Decimal,
    ) -> None:
        """Capture post-close snapshot, build record, persist. Best-effort.

        Called from ``_finalize_and_publish_close`` (and the manual-close
        path in ``_record_manual_close``) — must never raise into the close
        flow. Logs a warning on any failure and returns silently.
        """
        try:
            post = await self._snapshot_balances(use_cached=False)
            pre = self._pending_pre_snapshots.pop(trade.trade_id, None)
            rec = self._build_reconciliation_record(trade, pre, post, expected_pnl)
            await self._persist_reconciliation(rec)

            if rec.untouched_drift and self._publisher is not None:
                try:
                    drift_str = ", ".join(
                        f"{ex}=${float(d):+.4f}"
                        for ex, d in rec.untouched_drift.items()
                    )
                    await self._publisher.publish_alert(
                        (
                            f"⚠️ Untouched-exchange drift on {trade.symbol} close: "
                            f"{drift_str}"
                        ),
                        severity="warning",
                        alert_type="reconcile_drift",
                        symbol=trade.symbol,
                        payload={
                            "trade_id": trade.trade_id,
                            "untouched_drift": {
                                ex: str(d) for ex, d in rec.untouched_drift.items()
                            },
                        },
                    )
                except Exception as exc:
                    logger.debug(
                        f"[{trade.symbol}] drift alert publish failed: {exc}",
                    )

            logger.info(
                f"📒 Reconciliation [{trade.symbol}] {trade.trade_id}: "
                f"net_delta=${float(rec.net_delta):+.4f} "
                f"expected=${float(rec.expected_pnl):+.4f} "
                f"drift=${float(rec.drift):+.4f}"
                + (f" partial=true" if rec.partial else "")
                + (
                    f" untouched={list(rec.untouched_drift.keys())}"
                    if rec.untouched_drift
                    else ""
                ),
                extra={
                    "trade_id": trade.trade_id,
                    "symbol": trade.symbol,
                    "action": "reconciliation_recorded",
                },
            )
        except Exception as exc:
            logger.warning(
                f"[{trade.symbol}] reconciliation failed: {exc}",
                extra={"trade_id": trade.trade_id, "action": "reconcile_fail"},
            )
