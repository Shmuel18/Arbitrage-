let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export const connectWebSocket = (onMessage: (data: any) => void) => {
  // Prevent duplicate connections
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  // Clear any pending reconnect
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  ws = new WebSocket('ws://localhost:8000/ws');

  ws.onopen = () => {
    console.log('✅ WebSocket connected');
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
    } catch (error) {
      console.error('Error parsing WebSocket message:', error);
    }
  };

  ws.onerror = () => {
    // onclose will fire after this, handle reconnect there
  };

  ws.onclose = () => {
    console.log('❌ WebSocket disconnected');
    ws = null;
    // Reconnect after 5 seconds, but only if not already scheduled
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket(onMessage);
      }, 5000);
    }
  };
};

export const disconnectWebSocket = () => {
  if (ws) {
    ws.close();
    ws = null;
  }
};

export const sendWebSocketMessage = (message: any) => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
};
