let ws: WebSocket | null = null;

export const connectWebSocket = (onMessage: (data: any) => void) => {
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

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };

  ws.onclose = () => {
    console.log('❌ WebSocket disconnected');
    // Attempt to reconnect after 5 seconds
    setTimeout(() => connectWebSocket(onMessage), 5000);
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
