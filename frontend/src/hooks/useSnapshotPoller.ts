/**
 * useSnapshotPoller — REST polling hook with AbortController.
 *
 * Fires an immediate fetch on mount, then polls every 5 s.
 * Each cycle cancels the previous in-flight request.
 */
import { useCallback, useEffect, useRef } from 'react';
import axios from 'axios';
import {
  getBalances,
  getLogs,
  getOpportunities,
  getPnL,
  getPositions,
  getStatus,
  getSummary,
  getTrades,
} from '../services/api';
import { MarketAction } from './useMarketReducer';

const _POLL_INTERVAL_MS = 5000;

export function useSnapshotPoller(
  dispatch: (action: MarketAction) => void,
  pnlHoursRef: React.MutableRefObject<number>,
) {
  const abortCtrlRef = useRef<AbortController | null>(null);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  const fetchAll = useCallback(async () => {
    abortCtrlRef.current?.abort();
    const ctrl = new AbortController();
    abortCtrlRef.current = ctrl;
    const { signal } = ctrl;

    try {
      const hours = pnlHoursRef.current;
      const pnlPromise = getPnL(hours, signal);
      const dailyPnlPromise = hours === 24 ? pnlPromise : getPnL(24, signal);

      const [statusRes, balRes, oppRes, logsRes, summRes, posRes, pnlRes, dailyPnlRes, tradesRes] =
        await Promise.allSettled([
          getStatus(signal),
          getBalances(signal),
          getOpportunities(signal),
          getLogs(50, signal),
          getSummary(signal),
          getPositions(signal),
          pnlPromise,
          dailyPnlPromise,
          getTrades(20, undefined, signal),
        ]);

      if (signal.aborted) return;

      // Drop our pnl result if the user flipped the pill while this poll
      // was in flight — the response is for a now-stale hours window and
      // would visibly overwrite the chart the user just chose.
      const stalePnl = hours !== pnlHoursRef.current;

      dispatchRef.current({
        type: 'HTTP_FETCH_RESULT',
        payload: {
          status: statusRes,
          balances: balRes,
          opportunities: oppRes,
          logs: logsRes,
          summary: summRes,
          positions: posRes,
          pnl: stalePnl
            ? ({ status: 'rejected', reason: 'stale-pnl-hours' } as PromiseRejectedResult)
            : pnlRes,
          dailyPnl: dailyPnlRes,
          trades: tradesRes,
        },
      });

      // If every single request was rejected the API is unreachable — surface
      // a user-visible error so the dashboard doesn't silently show stale data.
      const allFailed = [statusRes, balRes, oppRes, logsRes, summRes, posRes, pnlRes, tradesRes]
        .every((r) => r.status === 'rejected');
      if (allFailed) {
        dispatchRef.current({ type: 'FETCH_ERROR', payload: 'Unable to reach the API server' });
      }
    } catch (error) {
      if (!axios.isCancel(error)) {
        console.error('Error fetching data:', error);
        dispatchRef.current({ type: 'FETCH_ERROR', payload: 'Network error — data may be stale' });
      }
    }
  }, [pnlHoursRef]);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, _POLL_INTERVAL_MS);
    return () => {
      clearInterval(interval);
      abortCtrlRef.current?.abort();
    };
  }, [fetchAll]);
}
