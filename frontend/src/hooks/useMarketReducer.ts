/**
 * useMarketReducer — pure-reducer state management for market data.
 *
 * All comparison helpers and state merging live here, keeping business logic
 * isolated from I/O concerns (WebSocket, REST polling).
 */
import { useReducer, useCallback } from 'react';
import { BotStatus, Trade } from '../types';

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
  [k: string]: unknown;
}

export interface OpportunitySet {
  opportunities: any[];
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
  positions: PositionRow[];
  trades: Trade[];
  tradesLoaded: boolean;
  lastFetchedAt: number;
}

/* ---------- WebSocket payload shape ---------- */

export interface WsFullUpdateData {
  status?: BotStatus;
  balances?: BalancesSet;
  opportunities?: OpportunitySet;
  positions?: PositionRow[];
  trades?: Trade[];
  logs?: LogEntry[];
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

export type MarketAction =
  | WsFullUpdateAction
  | WsStatusUpdateAction
  | HttpFetchResultAction
  | PnlUpdateAction;

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
  positions: [],
  trades: [],
  tradesLoaded: false,
  lastFetchedAt: Date.now(),
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
        if (prevOps.length !== newOps.length) return d.opportunities;
        if (prevOps.length === 0) return prev.opportunities;
        const pFirst = prevOps[0];
        const nFirst = newOps[0];
        const pLast = prevOps[prevOps.length - 1];
        const nLast = newOps[newOps.length - 1];
        if (
          pFirst?.symbol === nFirst?.symbol &&
          pFirst?.long_exchange === nFirst?.long_exchange &&
          pLast?.symbol === nLast?.symbol &&
          pLast?.long_exchange === nLast?.long_exchange &&
          prev.opportunities?.count === d.opportunities.count
        ) {
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
        if (
          prev.logs.length === d.logs.length &&
          prev.logs[0]?.timestamp === d.logs[0]?.timestamp
        ) {
          return prev.logs;
        }
        return d.logs;
      })();

      const newSummary =
        d.summary && d.summary.all_time_pnl !== undefined ? d.summary : prev.summary;
      const newPnl =
        d.pnl && Array.isArray(d.pnl.data_points) && d.pnl.data_points.length > 0
          ? d.pnl
          : prev.pnl;

      if (
        newStatus === prev.status &&
        newBalances === prev.balances &&
        newOpportunities === prev.opportunities &&
        newSummary === prev.summary &&
        newPnl === prev.pnl &&
        newLogs === prev.logs &&
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
        summary:
          summRes.status === 'fulfilled' && summRes.value?.total_trades != null
            ? summRes.value
            : prev.summary,
        positions: posRes.status === 'fulfilled' ? httpPositions : prev.positions,
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
      };
    }

    case 'PNL_UPDATE':
      return { ...prev, pnl: action.payload };

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
