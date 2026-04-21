import axios from 'axios';
import type { BotStatus, Trade } from '../types';

// Use relative URL so it works via ngrok / any host
const API_BASE_URL = '/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Attach read-token for protected telemetry endpoints.
// Priority: VITE_READ_TOKEN, fallback VITE_WS_TOKEN (same operational secret).
const readToken = (import.meta.env.VITE_READ_TOKEN as string | undefined)?.trim()
  || (import.meta.env.VITE_WS_TOKEN as string | undefined)?.trim();

const adminToken = (import.meta.env.VITE_ADMIN_TOKEN as string | undefined)?.trim()
  || (import.meta.env.VITE_WS_TOKEN as string | undefined)?.trim();
const commandToken = (import.meta.env.VITE_COMMAND_TOKEN as string | undefined)?.trim()
  || adminToken;
const configToken = (import.meta.env.VITE_CONFIG_TOKEN as string | undefined)?.trim()
  || adminToken;
const emergencyToken = (import.meta.env.VITE_EMERGENCY_TOKEN as string | undefined)?.trim()
  || adminToken;
const tradeToken = (import.meta.env.VITE_TRADE_TOKEN as string | undefined)?.trim()
  || adminToken;

if (readToken) {
  api.defaults.headers.common['X-Read-Token'] = readToken;
}

/**
 * Telegram Mini App auth — when the dashboard is opened inside Telegram,
 * TelegramProvider populates this singleton with the signed `initData`
 * querystring. The request interceptor below attaches it to every call.
 * Desktop (non-Mini-App) users skip this entirely and keep using the
 * X-Read-Token header.
 */
let telegramInitData: string | null = null;
export function setTelegramInitData(data: string | null): void {
  telegramInitData = data;
}
api.interceptors.request.use((config) => {
  if (telegramInitData) {
    // Axios 1.x headers are AxiosHeaders instances; .set() works for both
    // that and the plain object form used in older axios versions.
    if (config.headers && typeof (config.headers as { set?: unknown }).set === 'function') {
      (config.headers as unknown as { set: (k: string, v: string) => void }).set(
        'X-Telegram-Init-Data', telegramInitData,
      );
    } else {
      (config.headers as Record<string, string>)['X-Telegram-Init-Data'] = telegramInitData;
    }
  }
  return config;
});

/* ── Response types ────────────────────────────────────────────── */
export interface PositionsResponse { positions: Record<string, unknown>[] }
export interface TradesResponse { trades: Trade[] }
export interface TradeStatsResponse { total_trades: number; win_rate: number; [k: string]: unknown }
export interface CommandResponse { status: string; message: string }
export interface BalancesResponse { balances: Record<string, number>; total: number }
export interface LogsResponse { logs: { timestamp: string; message: string; level: string }[] }
export interface SummaryResponse { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number; all_time_pnl?: number; avg_pnl?: number }
export interface PnlResponse { data_points: { pnl: number; cumulative_pnl: number; unrealized?: number; realized?: number; timestamp: number }[]; total_pnl: number; unrealized_pnl?: number; realized_pnl?: number }
export interface OpportunitiesResponse { opportunities: Record<string, unknown>[]; count: number }

/* ── API functions ─────────────────────────────────────────────── */

export const getPositions = async (signal?: AbortSignal): Promise<PositionsResponse> => {
  const response = await api.get('/positions', { signal });
  return response.data;
};

export const closePosition = async (positionId: string): Promise<CommandResponse> => {
  const response = await api.delete(`/positions/${positionId}`, {
    headers: tradeToken ? { 'X-Trade-Token': tradeToken } : {},
  });
  return response.data;
};

export const getTrades = async (limit = 100, hours?: number, signal?: AbortSignal): Promise<TradesResponse> => {
  const params: Record<string, number> = { limit };
  if (hours) params.hours = hours;
  const response = await api.get('/trades', { params, signal });
  return response.data;
};

export const getTradeStats = async (): Promise<TradeStatsResponse> => {
  const response = await api.get('/trades/stats');
  return response.data;
};

export const sendBotCommand = async (action: string): Promise<CommandResponse> => {
  const response = await api.post('/controls/command', { action }, {
    headers: commandToken ? { 'X-Command-Token': commandToken } : {},
  });
  return response.data;
};

export const emergencyStop = async (): Promise<CommandResponse> => {
  const response = await api.post('/controls/emergency_stop', undefined, {
    headers: emergencyToken ? { 'X-Emergency-Token': emergencyToken } : {},
  });
  return response.data;
};

export const updateConfig = async (key: string, value: string | number | boolean): Promise<CommandResponse> => {
  const response = await api.post('/controls/config', { key, value }, {
    headers: configToken ? { 'X-Config-Token': configToken } : {},
  });
  return response.data;
};

export const getExchanges = async (): Promise<{ exchanges: unknown[] }> => {
  const response = await api.get('/controls/exchanges');
  return response.data;
};

export const getPerformance = async (hours = 24): Promise<unknown> => {
  const response = await api.get('/analytics/performance', { params: { hours } });
  return response.data;
};

export const getPnL = async (hours = 24, signal?: AbortSignal): Promise<PnlResponse> => {
  const response = await api.get('/analytics/pnl', { params: { hours }, signal });
  return response.data;
};

export const getSummary = async (signal?: AbortSignal): Promise<SummaryResponse> => {
  const response = await api.get('/analytics/summary', { signal });
  return response.data;
};

export const getOpportunities = async (signal?: AbortSignal): Promise<OpportunitiesResponse> => {
  const response = await api.get('/opportunities', { signal });
  return response.data;
};

export const getBalances = async (signal?: AbortSignal): Promise<BalancesResponse> => {
  const response = await api.get('/balances', { signal });
  return response.data;
};

export const getStatus = async (signal?: AbortSignal): Promise<BotStatus> => {
  const response = await api.get('/status', { signal });
  return response.data;
};

export const getLogs = async (limit = 50, signal?: AbortSignal): Promise<LogsResponse> => {
  const response = await api.get('/logs', { params: { limit }, signal });
  return response.data;
};

export default api;
