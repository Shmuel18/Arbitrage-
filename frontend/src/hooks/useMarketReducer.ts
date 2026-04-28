/**
 * useMarketReducer — pure-reducer state management for market data.
 *
 * All comparison helpers and state merging live here, keeping business logic
 * isolated from I/O concerns (WebSocket, REST polling).
 */
import { useReducer, useCallback } from 'react';
import { Alert, BotStatus, Trade } from '../types';

/* ---------- Shared sub-types ---------- */

export interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

export interface PositionRow {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_qty: string;
  short_qty: string;
  entry_edge_pct: string;
  state: string;
  next_funding_ms?: number | null;
  min_interval_hours?: number;
  long_next_funding_ms?: number | null;
  short_next_funding_ms?: number | null;
  long_interval_hours?: number;
  short_interval_hours?: number;
  [k: string]: unknown;
}

export interface Opportunity {
  symbol?: string;
  long_exchange?: string;
  short_exchange?: string;
  long_rate?: number;
  short_rate?: number;
  net_pct?: number;
  gross_pct?: number;
  funding_spread_pct?: number;
  immediate_spread_pct?: number;
  hourly_rate_pct?: number;
  min_interval_hours?: number;
  next_funding_ms?: number | null;
  long_next_funding_ms?: number | null;
  short_next_funding_ms?: number | null;
  long_interval_hours?: number;
  short_interval_hours?: number;
  qualified?: boolean;
  price?: number;
  mode?: string;
  fees_pct?: number;
  immediate_net_pct?: number;
  entry_tier?: string | null;
  price_spread_pct?: number | null;
  stale_price?: boolean;
  // Sizer/risk pre-flight result — set by scanner before publishing.
  // 'ready' = bot will try to enter; other values surface a reason
  // (insufficient_balance / lot_size_too_large / already_open) so the
  // dashboard can flag rows that look qualified but won't be executed.
  executable_status?: string | null;
  // Why qualified=False — set by the scanner gate that rejected the opp.
  // One of: vol_unknown / low_vol / adverse_basis / funding_spread_low
  // / funding_no_imminent / funding_stale / cherry_unsuitable / null.
  disqualify_reason?: string | null;
  [k: string]: unknown;
}

export interface OpportunitySet {
  opportunities: Opportunity[];
  count: number;
}

export interface BalancesSet {
  balances: Record<string, number>;
  total: number;
}

export interface SummaryData {
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  active_positions: number;
  uptime_hours: number;
  all_time_pnl?: number;
  avg_pnl?: number;
}

export interface PnlData {
  data_points: { pnl: number; cumulative_pnl: number; timestamp: number }[];
  total_pnl: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
}

export interface FullData {
  status: BotStatus;
  balances: BalancesSet | null;
  opportunities: OpportunitySet | null;
  summary: SummaryData | null;
  pnl: PnlData | null;
  dailyPnl: number;
  logs: LogEntry[];
  alerts: Alert[];
  positions: PositionRow[];
  trades: Trade[];
  tradesLoaded: boolean;
  lastFetchedAt: number;
  fetchError: string | null;
}

/* ---------- WebSocket payload shape ---------- */

export interface WsFullUpdateData {
  status?: BotStatus;
  balances?: BalancesSet;
  opportunities?: OpportunitySet;
  positions?: PositionRow[];
  trades?: Trade[];
  logs?: LogEntry[];
  alerts?: Alert[];
  summary?: SummaryData;
  pnl?: PnlData;
}

/* ---------- Reducer actions ---------- */

interface WsFullUpdateAction {
  type: 'WS_FULL_UPDATE';
  payload: WsFullUpdateData;
}

interface WsStatusUpdateAction {
  type: 'WS_STATUS_UPDATE';
  payload: BotStatus;
}

interface HttpFetchResultAction {
  type: 'HTTP_FETCH_RESULT';
  payload: {
    status: PromiseSettledResult<BotStatus>;
    balances: PromiseSettledResult<BalancesSet>;
    opportunities: PromiseSettledResult<OpportunitySet>;
    logs: PromiseSettledResult<{ logs: LogEntry[] }>;
    summary: PromiseSettledResult<SummaryData>;
    positions: PromiseSettledResult<any>;
    pnl: PromiseSettledResult<PnlData>;
    dailyPnl: PromiseSettledResult<PnlData>;
    trades: PromiseSettledResult<any>;
  };
}

interface PnlUpdateAction {
  type: 'PNL_UPDATE';
  payload: PnlData;
}

interface FetchErrorAction {
  type: 'FETCH_ERROR';
  payload: string;
}

export type MarketAction =
  | WsFullUpdateAction
  | WsStatusUpdateAction
  | HttpFetchResultAction
  | PnlUpdateAction
  | FetchErrorAction;

/* ---------- Comparison helpers ---------- */

function sameTradeIds(left: Trade[], right: Trade[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((trade, i) => trade.id === right[i]?.id);
}

function samePositionKeys(left: PositionRow[], right: PositionRow[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((position, i) => {
    const leftKey = position.id || position.symbol || '';
    const rightPos = right[i];
    const rightKey = (rightPos?.id || rightPos?.symbol || '') as string;
    return leftKey === rightKey;
  });
}

function sameExchangeList(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((exchange, i) => exchange === right[i]);
}

/* ---------- Extraction helpers ---------- */

function extractPositions(raw: any): PositionRow[] {
  if (!raw) return [];
  const arr = raw.positions;
  if (Array.isArray(arr)) return arr as PositionRow[];
  if (Array.isArray(raw)) return raw as unknown as PositionRow[];
  return [];
}

function extractTrades(raw: any): Trade[] {
  if (!raw) return [];
  const arr = raw.trades;
  if (Array.isArray(arr)) return arr;
  if (Array.isArray(raw)) return raw as unknown as Trade[];
  return [];
}

/* ---------- Initial state ---------- */

export const INITIAL_FULL_DATA: FullData = {
  status: { bot_running: false, connected_exchanges: [], active_positions: 0, uptime: 0 },
  balances: null,
  opportunities: null,
  summary: null,
  pnl: null,
  dailyPnl: 0,
  logs: [],
  alerts: [],
  positions: [],
  trades: [],
  tradesLoaded: false,
  lastFetchedAt: Date.now(),
  fetchError: null,
};

/* ---------- Reducer ---------- */

function marketReducer(prev: FullData, action: MarketAction): FullData {
  switch (action.type) {
    case 'WS_FULL_UPDATE': {
      const d = action.payload;

      const newStatus = (() => {
        if (!d.status) return prev.status;
        if (
          d.status.bot_running === prev.status.bot_running &&
          d.status.active_positions === prev.status.active_positions &&
          d.status.uptime === prev.status.uptime &&
          sameExchangeList(d.status.connected_exchanges, prev.status.connected_exchanges)
        ) {
          return prev.status;
        }
        return d.status;
      })();

      const newBalances = (() => {
        if (!d.balances) return prev.balances;
        if (prev.balances && prev.balances.total > 0 && d.balances.total <= 0) {
          return prev.balances;
        }
        if (prev.balances && d.balances.total === prev.balances.total) return prev.balances;
        return d.balances;
      })();

      const newOpportunities = (() => {
        if (!d.opportunities) return prev.opportunities;
        const prevOps = prev.opportunities?.opportunities || [];
        const newOps = d.opportunities.opportunities || [];
        if (prevOps.length === 0 && newOps.length === 0) return prev.opportunities;
        // Compare the displayed list (top 5) by fingerprint — ignore the total count
        // which fluctuates every scan cycle by ±10 and causes constant re-renders.
        // Build a stable fingerprint that is:
        //  • order-independent (sort keys before joining) — rank shuffles from
        //    price_spread_pct tiebreaking don't count as a change
        //  • coarse on net_pct (0.1 % resolution) — avoids flicker from tiny
        //    funding-rate drift between 8-hour resets
        //  • sensitive to stale_price and qualified flags — real status changes
        //    do trigger an update
        // Bucket next_funding timestamps to 10-minute windows.
        // This ensures a funding rollover (e.g. NOW → 8h away) breaks the
        // fingerprint and triggers a re-render, while sub-10-min drift
        // (normal countdown noise) does not cause unnecessary updates.
        const _10MIN = 600_000;
        const bucket = (ms: number | null | undefined) =>
          ms != null ? Math.floor(ms / _10MIN) : -1;
        const fingerprint = (ops: Opportunity[]) => {
          const keys = ops.slice(0, 5).map(o =>
            `${o.symbol}|${o.long_exchange}|${o.short_exchange}` +
            `|${o.stale_price ? 1 : 0}|${o.qualified ? 1 : 0}|${((o.net_pct ?? 0) * 10 | 0)}` +
            `|${bucket(o.long_next_funding_ms)}|${bucket(o.short_next_funding_ms)}`
          );
          return keys.sort().join(',');
        };
        if (fingerprint(prevOps) === fingerprint(newOps)) {
          return prev.opportunities;
        }
        return d.opportunities;
      })();

      const newPositions = (() => {
        let posArr: PositionRow[] | null = null;
        if (Array.isArray(d.positions)) {
          posArr = d.positions;
        } else if (
          d.positions &&
          typeof d.positions === 'object' &&
          Array.isArray((d.positions as any).positions)
        ) {
          posArr = (d.positions as any).positions;
        }
        if (posArr === null) return prev.positions;
        if (posArr.length === 0) return [];
        return samePositionKeys(prev.positions, posArr) ? prev.positions : posArr;
      })();

      const newTrades = (() => {
        const tradeList = Array.isArray(d.trades) && d.trades.length > 0 ? d.trades : null;
        if (!tradeList) return prev.trades;
        return sameTradeIds(prev.trades, tradeList) ? prev.trades : tradeList;
      })();

      const wsTradesLoaded = newTrades.length > 0 ? true : prev.tradesLoaded;

      const newLogs = (() => {
        if (!Array.isArray(d.logs) || d.logs.length === 0) return prev.logs;
        // Check both first AND last element — checking only the first missed
        // cases where new messages were appended with the same array length
        // (e.g. a sliding window where old entries drop off as new ones arrive).
        if (
          prev.logs.length === d.logs.length &&
          prev.logs[0]?.timestamp === d.logs[0]?.timestamp &&
          prev.logs[prev.logs.length - 1]?.timestamp === d.logs[d.logs.length - 1]?.timestamp &&
          prev.logs[prev.logs.length - 1]?.message === d.logs[d.logs.length - 1]?.message
        ) {
          return prev.logs;
        }
        return d.logs;
      })();

      const newAlerts = (() => {
        if (!Array.isArray(d.alerts) || d.alerts.length === 0) return prev.alerts;
        if (
          prev.alerts.length === d.alerts.length &&
          prev.alerts[0]?.id === d.alerts[0]?.id
        ) {
          return prev.alerts;
        }
        return d.alerts;
      })();

      // Guard: two sources (WS counter vs HTTP zrange scan) can temporarily
      // disagree on total_trades. Trade count only ever goes up — keep the max.
      // Note: do NOT gate on all_time_pnl being defined — some WS payloads
      // legitimately omit it, and blocking the entire summary update causes
      // win_rate / all_time_pnl to stay at 0 forever.
      const newSummary = (() => {
        if (!d.summary || d.summary.total_trades == null) return prev.summary;
        if (
          prev.summary &&
          d.summary.total_trades < prev.summary.total_trades
        ) {
          return { ...d.summary, total_trades: prev.summary.total_trades };
        }
        return d.summary;
      })();
      // PnL is NOT taken from WebSocket — the WS broadcast always sends 24h
      // data, which would overwrite the user's selected time range (7d/30d/All)
      // causing constant flickering.  PnL is only updated via REST polling
      // (HTTP_FETCH_RESULT) and explicit PNL_UPDATE actions.
      const newPnl = prev.pnl;

      if (
        newStatus === prev.status &&
        newBalances === prev.balances &&
        newOpportunities === prev.opportunities &&
        newSummary === prev.summary &&
        newPnl === prev.pnl &&
        newLogs === prev.logs &&
        newAlerts === prev.alerts &&
        newPositions === prev.positions &&
        newTrades === prev.trades &&
        wsTradesLoaded === prev.tradesLoaded
      ) {
        return prev;
      }

      return {
        ...prev,
        status: newStatus,
        balances: newBalances,
        opportunities: newOpportunities,
        summary: newSummary,
        pnl: newPnl,
        logs: newLogs,
        alerts: newAlerts,
        positions: newPositions,
        trades: newTrades,
        tradesLoaded: wsTradesLoaded,
      };
    }

    case 'WS_STATUS_UPDATE': {
      const sd = action.payload;
      if (
        sd.bot_running === prev.status.bot_running &&
        sd.active_positions === prev.status.active_positions
      ) {
        return prev;
      }
      return { ...prev, status: sd };
    }

    case 'HTTP_FETCH_RESULT': {
      const {
        status: statusRes,
        balances: balRes,
        opportunities: oppRes,
        logs: logsRes,
        summary: summRes,
        positions: posRes,
        pnl: pnlRes,
        dailyPnl: dailyPnlRes,
        trades: tradesRes,
      } = action.payload;

      const httpPositions =
        posRes.status === 'fulfilled' ? extractPositions(posRes.value) : [];
      const httpTrades =
        tradesRes.status === 'fulfilled' ? extractTrades(tradesRes.value) : [];

      return {
        ...prev,
        status: statusRes.status === 'fulfilled' ? statusRes.value : prev.status,
        balances: (() => {
          if (balRes.status !== 'fulfilled') return prev.balances;
          const incoming = balRes.value as BalancesSet;
          if (prev.balances && prev.balances.total > 0 && (!incoming || incoming.total <= 0)) {
            return prev.balances;
          }
          return incoming;
        })(),
        opportunities:
          oppRes.status === 'fulfilled' ? (oppRes.value as OpportunitySet) : prev.opportunities,
        logs: logsRes.status === 'fulfilled' ? logsRes.value.logs || [] : prev.logs,
        summary: (() => {
          if (summRes.status !== 'fulfilled' || summRes.value?.total_trades == null) return prev.summary;
          const incoming = summRes.value as SummaryData;
          // Never let an HTTP poll overwrite a higher count already known from WS.
          if (prev.summary && incoming.total_trades < prev.summary.total_trades) {
            return { ...incoming, total_trades: prev.summary.total_trades };
          }
          return incoming;
        })(),
        positions: posRes.status === 'fulfilled' ? httpPositions : prev.positions,
        // Guard against stale PnL responses from a poll that was fired before
        // the user clicked a new timeline pill. If the request was for a
        // different `hours` than the user's current selection, drop it.
        pnl: pnlRes.status === 'fulfilled' ? pnlRes.value : prev.pnl,
        dailyPnl:
          dailyPnlRes.status === 'fulfilled'
            ? dailyPnlRes.value.total_pnl || 0
            : prev.dailyPnl,
        lastFetchedAt: Date.now(),
        tradesLoaded: true,
        trades: (() => {
          if (httpTrades.length === 0) return prev.trades;
          return sameTradeIds(prev.trades, httpTrades) ? prev.trades : httpTrades;
        })(),
        // Clear any outstanding fetch error — reaching here means the API
        // responded (at least partially), so the connection is healthy.
        fetchError: null,
      };
    }

    case 'PNL_UPDATE':
      // Fence-based staleness guard lives in useMarketData — by the time the
      // action reaches us, we trust it's for the user's current range.
      return { ...prev, pnl: action.payload };

    case 'FETCH_ERROR':
      // Only set the error if it differs from the current one (avoid re-renders).
      return prev.fetchError === action.payload ? prev : { ...prev, fetchError: action.payload };

    default:
      return prev;
  }
}

/* ---------- Hook ---------- */

export function useMarketReducer() {
  const [data, dispatch] = useReducer(marketReducer, INITIAL_FULL_DATA);
  const stableDispatch = useCallback(
    (action: MarketAction) => dispatch(action),
    [],
  );
  return { data, dispatch: stableDispatch };
}
