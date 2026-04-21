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

  // Fences requests by incrementing on every click; responses check the fence
  // to drop late-arriving data from a previously-selected timeframe.
  const pnlRequestFenceRef = useRef<number>(0);

  const handlePnlHoursChange = useCallback((hours: number) => {
    setPnlHours(hours);
    pnlHoursRef.current = hours;
    // Fence supersedes previous in-flight user requests so a late response
    // from an earlier pill click can't overwrite the user's newer selection.
    const myFence = ++pnlRequestFenceRef.current;
    getPnL(hours).then((pnlRes) => {
      if (myFence !== pnlRequestFenceRef.current) return;
      dispatch({ type: 'PNL_UPDATE', payload: pnlRes });
    }).catch((err) => {
      if (!axios.isCancel(err)) {
        console.warn('[PnL] Failed to update chart for selected range:', err);
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
