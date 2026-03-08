import { useCallback, useEffect, useRef, useState } from 'react';
import { BotStatus, Trade } from '../types';
import { connectWebSocket, disconnectWebSocket, WsConnectionState } from '../services/websocket';
import {
  getBalances,
  getLogs,
  getOpportunities,
  getPnL,
  getPositions,
  getStatus,
  getSummary,
  getTrades,
} from '../services/api';

interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

interface PositionRow {
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

interface OpportunitySet {
  opportunities: any[];
  count: number;
}

interface BalancesSet {
  balances: Record<string, number>;
  total: number;
}

interface SummaryData {
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  active_positions: number;
  uptime_hours: number;
  all_time_pnl?: number;
  avg_pnl?: number;
}

interface PnlData {
  data_points: { pnl: number; cumulative_pnl: number; timestamp: number }[];
  total_pnl: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
}

interface WsFullUpdateData {
  status?: BotStatus;
  balances?: BalancesSet;
  opportunities?: OpportunitySet;
  positions?: PositionRow[];
  trades?: Trade[];
  logs?: LogEntry[];
  summary?: SummaryData;
  pnl?: PnlData;
}

interface WsMessage {
  type: 'full_update' | 'status_update' | string;
  data?: WsFullUpdateData & BotStatus & Record<string, unknown>;
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

const _POLL_INTERVAL_MS = 5000;

interface MarketDataState {
  data: FullData;
  pnlHours: number;
  handlePnlHoursChange: (hours: number) => void;
  wsConnection: WsConnectionState;
  lastWsMessageAt: number | null;
}

export function useMarketData(): MarketDataState {
  const [pnlHours, setPnlHours] = useState<number>(24);
  const pnlHoursRef = useRef<number>(pnlHours);
  const [wsConnection, setWsConnection] = useState<WsConnectionState>('disconnected');
  const [lastWsMessageAt, setLastWsMessageAt] = useState<number | null>(null);

  useEffect(() => {
    pnlHoursRef.current = pnlHours;
  }, [pnlHours]);

  const [data, setData] = useState<FullData>({
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
  });

  const handlePnlHoursChange = useCallback((hours: number) => {
    setPnlHours(hours);
    pnlHoursRef.current = hours;
    getPnL(hours)
      .then((pnlRes) => {
        setData((prev) => ({ ...prev, pnl: pnlRes }));
      })
      .catch(() => {
        // Next poll retry handles transient failures.
      });
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      const hours = pnlHoursRef.current;
      const pnlPromise = getPnL(hours);
      const dailyPnlPromise = hours === 24 ? pnlPromise : getPnL(24);

      const [statusRes, balRes, oppRes, logsRes, summRes, posRes, pnlRes, dailyPnlRes, tradesRes] =
        await Promise.allSettled([
          getStatus(),
          getBalances(),
          getOpportunities(),
          getLogs(50),
          getSummary(),
          getPositions(),
          pnlPromise,
          dailyPnlPromise,
          getTrades(20),
        ]);

      const extractPositions = (): PositionRow[] => {
        if (posRes.status !== 'fulfilled') return [];
        const raw = posRes.value;
        if (!raw) return [];
        const arr = raw.positions;
        if (Array.isArray(arr)) return arr as PositionRow[];
        if (Array.isArray(raw)) return raw as unknown as PositionRow[];
        return [];
      };

      const extractTrades = (): Trade[] => {
        if (tradesRes.status !== 'fulfilled') return [];
        const raw = tradesRes.value;
        if (!raw) return [];
        const arr = raw.trades;
        if (Array.isArray(arr)) return arr;
        if (Array.isArray(raw)) return raw as unknown as Trade[];
        return [];
      };

      const httpPositions = extractPositions();
      const httpTrades = extractTrades();

      setData((prev) => ({
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
        opportunities: oppRes.status === 'fulfilled' ? (oppRes.value as OpportunitySet) : prev.opportunities,
        logs: logsRes.status === 'fulfilled' ? logsRes.value.logs || [] : prev.logs,
        summary:
          summRes.status === 'fulfilled' && summRes.value?.total_trades != null
            ? summRes.value
            : prev.summary,
        // Trust a successful HTTP response even if it returns [] (trade closed).
        // Only keep prev when the request itself failed (non-fulfilled).
        positions: posRes.status === 'fulfilled' ? httpPositions : prev.positions,
        pnl: pnlRes.status === 'fulfilled' ? pnlRes.value : prev.pnl,
        dailyPnl:
          dailyPnlRes.status === 'fulfilled' ? dailyPnlRes.value.total_pnl || 0 : prev.dailyPnl,
        lastFetchedAt: Date.now(),
        tradesLoaded: true,
        trades: (() => {
          if (httpTrades.length === 0) return prev.trades;
          const prevIds = prev.trades.map((t) => t.id).join(',');
          const newIds = httpTrades.map((t) => t.id).join(',');
          return prevIds === newIds ? prev.trades : httpTrades;
        })(),
      }));
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []);

  useEffect(() => {
    connectWebSocket(
      (raw) => {
        setLastWsMessageAt(Date.now());
        const msg = raw as unknown as WsMessage;

        if (msg.type === 'full_update' && msg.data) {
          const d = msg.data;
          setData((prev) => {
            const newStatus = (() => {
              if (!d.status) return prev.status;
              if (
                d.status.bot_running === prev.status.bot_running &&
                d.status.active_positions === prev.status.active_positions &&
                d.status.uptime === prev.status.uptime &&
                d.status.connected_exchanges.length === prev.status.connected_exchanges.length &&
                d.status.connected_exchanges.every((e, i) => e === prev.status.connected_exchanges[i])
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
              const makeKey = (list: any[]) =>
                list
                  .map(
                    (o) =>
                      `${o.symbol}_${o.long_exchange}_${o.short_exchange}_${Number(
                        o.immediate_spread_pct ?? 0,
                      ).toFixed(4)}`,
                  )
                  .join('|');
              const prevKey = makeKey(prev.opportunities?.opportunities || []);
              const newKey = makeKey(d.opportunities.opportunities || []);
              return prevKey === newKey ? prev.opportunities : d.opportunities;
            })();

            const newPositions = (() => {
              // posArr === null  → field absent from WS payload (keep prev)
              // posArr.length === 0 → explicitly empty (trade closed — clear!)
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
              if (posArr === null) return prev.positions;  // field absent — keep prev
              if (posArr.length === 0) return [];           // server says 0 positions — clear
              const prevKey = prev.positions.map((p) => p.id || p.symbol || '').join(',');
              const newKey = posArr.map((p) => p.id || p.symbol || '').join(',');
              return prevKey === newKey ? prev.positions : posArr;
            })();

            const newTrades = (() => {
              const tradeList = Array.isArray(d.trades) && d.trades.length > 0 ? d.trades : null;
              if (!tradeList) return prev.trades;
              const prevIds = prev.trades.map((x) => x.id).join(',');
              const newIds = tradeList.map((x) => x.id).join(',');
              return prevIds === newIds ? prev.trades : tradeList;
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
          });
        } else if (msg.type === 'status_update') {
          setData((prev) => {
            const sd = msg.data as BotStatus | undefined;
            if (!sd) return prev;
            if (
              sd.bot_running === prev.status.bot_running &&
              sd.active_positions === prev.status.active_positions
            ) {
              return prev;
            }
            return { ...prev, status: sd };
          });
        }
      },
      (state) => {
        setWsConnection(state);
      },
    );

    fetchAll();
    const interval = setInterval(fetchAll, _POLL_INTERVAL_MS);

    return () => {
      clearInterval(interval);
      disconnectWebSocket();
      setWsConnection('disconnected');
    };
  }, [fetchAll]);

  return {
    data,
    pnlHours,
    handlePnlHoursChange,
    wsConnection,
    lastWsMessageAt,
  };
}
