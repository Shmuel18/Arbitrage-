import { afterEach, describe, expect, it, vi } from 'vitest';

describe('api client auth headers', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('uses VITE_READ_TOKEN when configured', async () => {
    vi.stubEnv('VITE_READ_TOKEN', 'read-token');
    vi.stubEnv('VITE_WS_TOKEN', 'ws-token-fallback');

    const mod = await import('./api');
    const client = mod.default;
    const headers = client.defaults.headers.common as Record<string, string>;

    expect(headers['X-Read-Token']).toBe('read-token');
  });

  it('falls back to VITE_WS_TOKEN when VITE_READ_TOKEN is missing', async () => {
    vi.stubEnv('VITE_READ_TOKEN', '');
    vi.stubEnv('VITE_WS_TOKEN', 'ws-token');

    const mod = await import('./api');
    const client = mod.default;
    const headers = client.defaults.headers.common as Record<string, string>;

    expect(headers['X-Read-Token']).toBe('ws-token');
  });

  it('sends X-Command-Token for bot command calls', async () => {
    vi.stubEnv('VITE_COMMAND_TOKEN', 'cmd-token');
    vi.stubEnv('VITE_ADMIN_TOKEN', 'admin-token');

    const mod = await import('./api');
    const client = mod.default;
    const postSpy = vi.spyOn(client, 'post').mockResolvedValue({
      data: { status: 'success', message: 'ok' },
    });

    await mod.sendBotCommand('start');

    expect(postSpy).toHaveBeenCalledWith(
      '/controls/command',
      { action: 'start' },
      { headers: { 'X-Command-Token': 'cmd-token' } },
    );
  });

  it('sends X-Config-Token for config updates', async () => {
    vi.stubEnv('VITE_CONFIG_TOKEN', 'cfg-token');

    const mod = await import('./api');
    const client = mod.default;
    const postSpy = vi.spyOn(client, 'post').mockResolvedValue({
      data: { status: 'success', message: 'ok' },
    });

    await mod.updateConfig('mode', 'paper');

    expect(postSpy).toHaveBeenCalledWith(
      '/controls/config',
      { key: 'mode', value: 'paper' },
      { headers: { 'X-Config-Token': 'cfg-token' } },
    );
  });

  it('sends X-Trade-Token for close position calls', async () => {
    vi.stubEnv('VITE_TRADE_TOKEN', 'trade-token');

    const mod = await import('./api');
    const client = mod.default;
    const deleteSpy = vi.spyOn(client, 'delete').mockResolvedValue({
      data: { status: 'success', message: 'ok' },
    });

    await mod.closePosition('pos-123');

    expect(deleteSpy).toHaveBeenCalledWith(
      '/positions/pos-123',
      { headers: { 'X-Trade-Token': 'trade-token' } },
    );
  });
});
