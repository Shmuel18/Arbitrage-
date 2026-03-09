/**
 * useWsFeed — WebSocket connection lifecycle hook.
 *
 * Manages connect/disconnect, tracks connection state and last-message
 * timestamp, and dispatches incoming messages to the market reducer.
 */
import { useEffect, useRef, useState } from 'react';
import { BotStatus } from '../types';
import { connectWebSocket, disconnectWebSocket, WsConnectionState } from '../services/websocket';
import { MarketAction, WsFullUpdateData } from './useMarketReducer';

interface WsMessage {
  type: 'full_update' | 'status_update' | string;
  data?: WsFullUpdateData & BotStatus & Record<string, unknown>;
}

export function useWsFeed(dispatch: (action: MarketAction) => void) {
  const [wsConnection, setWsConnection] = useState<WsConnectionState>('disconnected');
  const [lastWsMessageAt, setLastWsMessageAt] = useState<number | null>(null);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  useEffect(() => {
    connectWebSocket(
      (raw) => {
        setLastWsMessageAt(Date.now());
        const msg = raw as unknown as WsMessage;

        if (msg.type === 'full_update' && msg.data) {
          dispatchRef.current({ type: 'WS_FULL_UPDATE', payload: msg.data });
        } else if (msg.type === 'status_update' && msg.data) {
          dispatchRef.current({
            type: 'WS_STATUS_UPDATE',
            payload: msg.data as unknown as BotStatus,
          });
        }
      },
      (state) => {
        setWsConnection(state);
      },
    );

    return () => {
      disconnectWebSocket();
      setWsConnection('disconnected');
    };
  }, []);

  return { wsConnection, lastWsMessageAt };
}
