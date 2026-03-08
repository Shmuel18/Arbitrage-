let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 30000; // 30s cap
const BASE_RECONNECT_DELAY = 1000; // 1s base

/* ── Message schema guard ─────────────────────────────────────────
   Validates that a parsed WS payload has the minimum expected shape.
   Rejects blobs, null, arrays, and messages missing required fields. */
function isValidWsMessage(v: unknown): v is Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
  const msg = v as Record<string, unknown>;
  if (typeof msg['type'] !== 'string') return false;
  // 'full_update' messages must carry a non-null data object
  if (msg['type'] === 'full_update' && (msg['data'] == null || typeof msg['data'] !== 'object')) return false;
  return true;
}

export const connectWebSocket = (onMessage: (data: Record<string, unknown>) => void) => {
  // Prevent duplicate connections
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  // Clear any pending reconnect
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  // Dynamic WebSocket URL — works on localhost AND via ngrok/any host
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('✅ WebSocket connected');
    reconnectAttempts = 0; // reset backoff on successful connect
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as unknown;
      if (!isValidWsMessage(data)) {
        console.warn('WebSocket: dropping malformed message', typeof data);
        return;
      }
      onMessage(data);
    } catch (error) {
      console.error('WebSocket: failed to parse message:', error);
    }
  };

  ws.onerror = () => {
    // onclose will fire after this, handle reconnect there
  };

  ws.onclose = () => {
    console.log('❌ WebSocket disconnected');
    ws = null;
    // Exponential backoff with jitter, capped at MAX_RECONNECT_DELAY
    if (!reconnectTimer) {
      const delay = Math.min(
        BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttempts) + Math.random() * 500,
        MAX_RECONNECT_DELAY,
      );
      reconnectAttempts++;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket(onMessage);
      }, delay);
    }
  };
};

export const disconnectWebSocket = () => {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  reconnectAttempts = 0;
  if (ws) {
    ws.close();
    ws = null;
  }
};
