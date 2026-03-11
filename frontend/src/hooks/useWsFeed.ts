/**
 * useWsFeed — WebSocket connection lifecycle hook.
 *
 * Manages connect/disconnect, tracks connection state and last-message
 * timestamp, and dispatches incoming messages to the market reducer.
 *
 * Throttling strategy:
 *   • full_update  → at most 1 dispatch per FULL_UPDATE_INTERVAL_MS (300ms).
 *     Every WS message updates the buffer; only the LATEST payload is flushed.
 *     This prevents React cascades when the backend streams faster than 60fps.
 *   • status_update → at most 1 dispatch per STATUS_UPDATE_INTERVAL_MS (1000ms).
 *   • lastWsMessageAt is updated on EVERY raw message (unthrottled) so the
 *     connection health indicator stays accurate.
 */
import { useEffect, useRef, useState, startTransition } from 'react';
import { BotStatus } from '../types';
import { connectWebSocket, disconnectWebSocket, WsConnectionState } from '../services/websocket';
import { MarketAction, WsFullUpdateData } from './useMarketReducer';

const FULL_UPDATE_INTERVAL_MS = 300;
const STATUS_UPDATE_INTERVAL_MS = 1_000;

interface WsMessage {
  type: 'full_update' | 'status_update' | string;
  data?: WsFullUpdateData & BotStatus & Record<string, unknown>;
}

export function useWsFeed(dispatch: (action: MarketAction) => void) {
  const [wsConnection, setWsConnection] = useState<WsConnectionState>('disconnected');
  const [lastWsMessageAt, setLastWsMessageAt] = useState<number | null>(null);
  const [wsAttempts, setWsAttempts] = useState(0);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  // Throttle buffers — hold the latest payload and the pending timer id.
  const fullUpdateBuf = useRef<WsFullUpdateData | null>(null);
  const fullUpdateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusUpdateBuf = useRef<BotStatus | null>(null);
  const statusUpdateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Guard against setState calls that arrive after the component unmounts
    // (e.g. if the WebSocket fires onclose after the cleanup function runs).
    let isMounted = true;

    connectWebSocket(
      (raw) => {
        if (!isMounted) return;
        // Always update the health timestamp — unthrottled.
        setLastWsMessageAt(Date.now());
        const msg = raw as unknown as WsMessage;

        if (msg.type === 'full_update' && msg.data) {
          // Buffer latest payload; schedule flush only if not already pending.
          fullUpdateBuf.current = msg.data;
          if (fullUpdateTimer.current === null) {
            fullUpdateTimer.current = setTimeout(() => {
              const payload = fullUpdateBuf.current;
              fullUpdateBuf.current = null;
              fullUpdateTimer.current = null;
              if (payload) {
                startTransition(() => {
                  dispatchRef.current({ type: 'WS_FULL_UPDATE', payload });
                });
              }
            }, FULL_UPDATE_INTERVAL_MS);
          }

        } else if (msg.type === 'status_update' && msg.data) {
          statusUpdateBuf.current = msg.data as unknown as BotStatus;
          if (statusUpdateTimer.current === null) {
            statusUpdateTimer.current = setTimeout(() => {
              const payload = statusUpdateBuf.current;
              statusUpdateBuf.current = null;
              statusUpdateTimer.current = null;
              if (payload) {
                startTransition(() => {
                  dispatchRef.current({ type: 'WS_STATUS_UPDATE', payload });
                });
              }
            }, STATUS_UPDATE_INTERVAL_MS);
          }
        }
      },
      (state, attempt) => {
        if (!isMounted) return;
        setWsConnection(state);
        if (attempt !== undefined) setWsAttempts(attempt);
        else if (state === 'connected') setWsAttempts(0);
      },
    );

    return () => {
      isMounted = false;
      // Flush any pending payloads before teardown so state stays consistent.
      if (fullUpdateTimer.current !== null) {
        clearTimeout(fullUpdateTimer.current);
        fullUpdateTimer.current = null;
        if (fullUpdateBuf.current) {
          dispatchRef.current({ type: 'WS_FULL_UPDATE', payload: fullUpdateBuf.current });
          fullUpdateBuf.current = null;
        }
      }
      if (statusUpdateTimer.current !== null) {
        clearTimeout(statusUpdateTimer.current);
        statusUpdateTimer.current = null;
        if (statusUpdateBuf.current) {
          dispatchRef.current({ type: 'WS_STATUS_UPDATE', payload: statusUpdateBuf.current });
          statusUpdateBuf.current = null;
        }
      }
      disconnectWebSocket();
      setWsConnection('disconnected');
    };
  }, []);

  return { wsConnection, lastWsMessageAt, wsAttempts };
}

