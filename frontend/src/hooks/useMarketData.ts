/**
 * useMarketData — orchestrator hook.
 *
 * Composes useMarketReducer, useWsFeed, and useSnapshotPoller into
 * the single interface that App.tsx consumes.
 */
import { useCallback, useRef, useState } from 'react';
import axios from 'axios';
import { WsConnectionState } from '../services/websocket';
import { getPnL } from '../services/api';
import { useMarketReducer } from './useMarketReducer';
import { useWsFeed } from './useWsFeed';
import { useSnapshotPoller } from './useSnapshotPoller';

// Re-export types that consumers depend on.
export type { FullData } from './useMarketReducer';

interface MarketDataState {
  data: import('./useMarketReducer').FullData;
  pnlHours: number;
  handlePnlHoursChange: (hours: number) => void;
  wsConnection: WsConnectionState;
  lastWsMessageAt: number | null;
  wsAttempts: number;
}

export function useMarketData(): MarketDataState {
  const [pnlHours, setPnlHours] = useState<number>(24);
  const pnlHoursRef = useRef<number>(pnlHours);

  const { data, dispatch } = useMarketReducer();
  const { wsConnection, lastWsMessageAt, wsAttempts } = useWsFeed(dispatch);
  useSnapshotPoller(dispatch, pnlHoursRef);

  const handlePnlHoursChange = useCallback((hours: number) => {
    setPnlHours(hours);
    pnlHoursRef.current = hours;
    const ctrl = new AbortController();
    getPnL(hours, ctrl.signal)
      .then((pnlRes) => {
        dispatch({ type: 'PNL_UPDATE', payload: pnlRes });
      })
      .catch((err) => {
        if (!axios.isCancel(err)) {
          // Next poll retry handles transient failures.
        }
      });
  }, [dispatch]);

  return {
    data,
    pnlHours,
    handlePnlHoursChange,
    wsConnection,
    lastWsMessageAt,
    wsAttempts,
  };
}
