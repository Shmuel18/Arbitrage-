import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { connectWebSocket, disconnectWebSocket } from './websocket';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }
}

describe('websocket service', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    vi.stubEnv('VITE_WS_TOKEN', 'token-123');
  });

  afterEach(() => {
    disconnectWebSocket();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('connects on /ws and sets token cookie', () => {
    const onMsg = vi.fn();
    const onConn = vi.fn();

    connectWebSocket(onMsg, onConn);

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url.endsWith('/ws')).toBe(true);
    expect(document.cookie).toContain('trinity_ws_token=token-123');
  });

  it('prevents duplicate active websocket connections', () => {
    const onMsg = vi.fn();

    connectWebSocket(onMsg);
    MockWebSocket.instances[0].open();
    connectWebSocket(onMsg);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('uses bare /ws path when token is not configured', () => {
    vi.stubEnv('VITE_WS_TOKEN', '');

    const onMsg = vi.fn();
    connectWebSocket(onMsg);

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url.endsWith('/ws')).toBe(true);
  });
});
