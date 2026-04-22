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

/**
 * Watchdog: if we haven't received any WS message for this long, the
 * connection is almost certainly dead (backend broadcasts every 2s).
 * Force-close it so the reconnect logic kicks in. Protects against
 * browser background throttling, NAT rebinding, and stale TCP sockets
 * that don't surface as onclose events.
 */
const WS_STALE_THRESHOLD_MS = 30_000;
const WS_WATCHDOG_INTERVAL_MS = 10_000;

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

  // Mirror of lastWsMessageAt in a ref so the watchdog interval can read
  // the current value without re-registering on every state change.
  const lastMsgRef = useRef<number | null>(null);
  const connectionRef = useRef<WsConnectionState>('disconnected');
  connectionRef.current = wsConnection;

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
        const ts = Date.now();
        setLastWsMessageAt(ts);
        lastMsgRef.current = ts;
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

    // ── Watchdog: force reconnect if no WS message for > 30s ─────
    // Backend broadcasts every 2s; 30s of silence = dead socket even if
    // the browser thinks it's still open (NAT rebind, background throttle).
    const watchdogId = setInterval(() => {
      if (!isMounted) return;
      if (connectionRef.current !== 'connected') return;
      const last = lastMsgRef.current;
      if (last === null) return;
      const age = Date.now() - last;
      if (age > WS_STALE_THRESHOLD_MS) {
        console.warn(
          `[ws-watchdog] stale ${age}ms > ${WS_STALE_THRESHOLD_MS}ms — forcing reconnect`,
        );
        // Mark disconnected so UI reflects state immediately; the WS close
        // handler in websocket.ts will then trigger automatic reconnect.
        setWsConnection('reconnecting');
        disconnectWebSocket();
        // Reset timestamp so we don't spam reconnects during the backoff
        lastMsgRef.current = Date.now();
        // Kick off a fresh connect (disconnectWebSocket set manualClose=true,
        // which normally prevents reconnect — call connectWebSocket directly).
        connectWebSocket(
          (raw) => {
            if (!isMounted) return;
            const ts = Date.now();
            setLastWsMessageAt(ts);
            lastMsgRef.current = ts;
            const msg = raw as unknown as WsMessage;
            if (msg.type === 'full_update' && msg.data) {
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
      }
    }, WS_WATCHDOG_INTERVAL_MS);

    return () => {
      isMounted = false;
      clearInterval(watchdogId);
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

