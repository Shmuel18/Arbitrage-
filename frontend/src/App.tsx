import React, { useState, useEffect, useCallback, Component, ErrorInfo, ReactNode } from 'react';
import Dashboard from './components/Dashboard';
import { BotStatus, Trade } from './types';
import { connectWebSocket, disconnectWebSocket } from './services/websocket';
import { getOpportunities, getBalances, getLogs, getSummary, getPositions, getPnL, getTrades, getStatus } from './services/api';
import './App.css';

/* ── Error Boundary ──────────────────────────────────────────────── */
interface ErrorBoundaryState { hasError: boolean; error?: Error }

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, textAlign: 'center', color: '#ef4444' }}>
          <h2>Something went wrong</h2>
          <pre style={{ fontSize: 12, color: '#94a3b8', marginTop: 12 }}>
            {this.state.error?.message}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{ marginTop: 20, padding: '8px 20px', borderRadius: 8, background: '#3b82f6', color: '#fff', border: 'none', cursor: 'pointer' }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

interface LogEntry { timestamp: string; message: string; level: string }
interface PositionRow { id: string; symbol: string; long_exchange: string; short_exchange: string; long_qty: string; short_qty: string; entry_edge_pct: string; state: string; [k: string]: unknown }
interface OpportunitySet { opportunities: any[]; count: number }
interface BalancesSet { balances: Record<string, number>; total: number }
interface SummaryData { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number; all_time_pnl?: number; avg_pnl?: number }
interface PnlData { data_points: { pnl: number; cumulative_pnl: number; timestamp: number }[]; total_pnl: number; unrealized_pnl?: number; realized_pnl?: number }

/** Shape of `msg.data` in a WebSocket full_update message */
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

/** Shape of a WebSocket message */
interface WsMessage {
  type: string;
  data?: WsFullUpdateData & Record<string, unknown>;
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

function App() {
  const [pnlHours, setPnlHours] = useState<number>(24);
  // Ref tracks pnlHours so fetchAll always reads the latest value
  // without restarting the polling interval on every hour change.
  const pnlHoursRef = React.useRef(pnlHours);
  // When user changes range, update ref AND trigger an immediate PnL fetch
  // so the chart updates instantly instead of waiting up to 5s for next poll.
  const handlePnlHoursChange = useCallback((hours: number) => {
    setPnlHours(hours);
    pnlHoursRef.current = hours;
    // Fire-and-forget: fetch only PnL with new range, update state immediately
    getPnL(hours).then(pnlRes => {
      setData(prev => ({ ...prev, pnl: pnlRes }));
    }).catch(() => {/* next poll will retry */});
  }, []);
  React.useEffect(() => { pnlHoursRef.current = pnlHours; }, [pnlHours]);
  const [data, setData] = useState<FullData>({
    status: { bot_running: false, connected_exchanges: [], active_positions: 0, uptime: 0 },
    balances: null, opportunities: null, summary: null, pnl: null, dailyPnl: 0,
    logs: [], positions: [], trades: [], tradesLoaded: false,
    lastFetchedAt: Date.now(),
  });

  const fetchAll = useCallback(async () => {
    try {
      const hours = pnlHoursRef.current;
      const pnlPromise = getPnL(hours);
      const dailyPnlPromise = hours === 24 ? pnlPromise : getPnL(24);

      const [statusRes, balRes, oppRes, logsRes, summRes, posRes, pnlRes, dailyPnlRes, tradesRes] = await Promise.allSettled([
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

      // ── Defensive positions extraction (handle both array & dict) ──
      const extractPositions = (): PositionRow[] => {
        if (posRes.status !== 'fulfilled') return [];
        const raw = posRes.value;
        if (!raw) return [];
        // API returns { positions: [...] } — extract the array
        const arr = raw.positions;
        if (Array.isArray(arr)) return arr as PositionRow[];
        // Fallback: raw itself might be an array
        if (Array.isArray(raw)) return raw as unknown as PositionRow[];
        return [];
      };

      // ── Defensive trades extraction ──
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

      setData(prev => ({
        ...prev,
        status: statusRes.status === 'fulfilled' ? statusRes.value : prev.status,
        balances: (() => {
          if (balRes.status !== 'fulfilled') return prev.balances;
          const incoming = balRes.value as BalancesSet;
          // Guard: don't replace good balances with empty/zero data
          if (prev.balances && prev.balances.total > 0 && (!incoming || incoming.total <= 0)) {
            return prev.balances;
          }
          return incoming;
        })(),
        opportunities: oppRes.status === 'fulfilled' ? oppRes.value as OpportunitySet : prev.opportunities,
        logs: logsRes.status === 'fulfilled' ? (logsRes.value.logs || []) : prev.logs,
        summary: summRes.status === 'fulfilled' && summRes.value?.total_trades != null ? summRes.value : prev.summary,
        positions: httpPositions.length > 0 ? httpPositions : prev.positions,
        pnl: pnlRes.status === 'fulfilled' ? pnlRes.value : prev.pnl,
        dailyPnl: dailyPnlRes.status === 'fulfilled' ? (dailyPnlRes.value.total_pnl || 0) : prev.dailyPnl,
        lastFetchedAt: Date.now(),
        tradesLoaded: true,
        trades: (() => {
          if (httpTrades.length === 0) return prev.trades;
          const prevIds = prev.trades.map((t: Trade) => t.id).join(',');
          const newIds = httpTrades.map((t: Trade) => t.id).join(',');
          return prevIds === newIds ? prev.trades : httpTrades;
        })(),
      }));
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []); // no deps — pnlHours read via ref to avoid interval restart

  useEffect(() => {
    connectWebSocket((raw) => {
      const msg = raw as unknown as WsMessage;
      if (msg.type === 'full_update' && msg.data) {
        const d = msg.data;
        setData(prev => {
          // ── Status: only swap reference when a field actually changed ──────────
          const newStatus = (() => {
            if (!d.status) return prev.status;
            if (
              d.status.bot_running === prev.status.bot_running &&
              d.status.active_positions === prev.status.active_positions &&
              d.status.uptime === prev.status.uptime &&
              d.status.connected_exchanges.length === prev.status.connected_exchanges.length &&
              d.status.connected_exchanges.every((e, i) => e === prev.status.connected_exchanges[i])
            ) return prev.status;
            return d.status;
          })();

          // ── Balances: only swap when total changed, never downgrade to zero ──
          const newBalances = (() => {
            if (!d.balances) return prev.balances;
            // Guard: don't replace good balances with empty/zero data
            if (prev.balances && prev.balances.total > 0 && d.balances.total <= 0) {
              return prev.balances;
            }
            if (prev.balances && d.balances.total === prev.balances.total) return prev.balances;
            return d.balances;
          })();

          // ── Opportunities: only swap when the qualified set changes ──────────
          const newOpportunities = (() => {
            if (!d.opportunities) return prev.opportunities;
            const makeKey = (list: any[]) => list.map((o) => `${o.symbol}_${o.long_exchange}_${o.short_exchange}_${(Number(o.immediate_spread_pct ?? 0)).toFixed(4)}`).join('|');
            const prevKey = makeKey(prev.opportunities?.opportunities || []);
            const newKey  = makeKey(d.opportunities.opportunities || []);
            return prevKey === newKey ? prev.opportunities : d.opportunities;
          })();

          // ── Positions: defensively extract array, swap when IDs change ───
          const newPositions = (() => {
            // Handle both array and dict-wrapped formats
            let posArr: PositionRow[] | null = null;
            if (Array.isArray(d.positions)) {
              posArr = d.positions;
            } else if (d.positions && typeof d.positions === 'object' && Array.isArray((d.positions as any).positions)) {
              posArr = (d.positions as any).positions;
            }
            if (!posArr || posArr.length === 0) return prev.positions;
            const prevKey = prev.positions.map((p: PositionRow) => p.id || p.symbol || '').join(',');
            const newKey  = posArr.map((p: PositionRow) => p.id || p.symbol || '').join(',');
            return prevKey === newKey ? prev.positions : posArr;
          })();

          // ── Trades: only swap when IDs change (prevents flicker) ─────────────
          const newTrades = (() => {
            const t = Array.isArray(d.trades) && d.trades.length > 0 ? d.trades : null;
            if (!t) return prev.trades;
            const prevIds = prev.trades.map((x: Trade) => x.id).join(',');
            const newIds  = t.map((x: Trade) => x.id).join(',');
            return prevIds === newIds ? prev.trades : t;
          })();

          // ── Mark tradesLoaded when WS delivers trades ────────────────────────
          const wsTradesLoaded = newTrades.length > 0 ? true : prev.tradesLoaded;

          // ── Logs: only swap when newest message changed ──────────────────────
          const newLogs = (() => {
            if (!Array.isArray(d.logs) || d.logs.length === 0) return prev.logs;
            if (prev.logs.length === d.logs.length && prev.logs[0]?.timestamp === d.logs[0]?.timestamp) return prev.logs;
            return d.logs;
          })();

          // ── Summary: only accept from WS if it carries computed fields ───────
          const newSummary = (d.summary && d.summary.all_time_pnl !== undefined) ? d.summary : prev.summary;

          // ── PnL: only accept if full structure present ───────────────────────
          const newPnl = (d.pnl && Array.isArray(d.pnl.data_points) && d.pnl.data_points.length > 0) ? d.pnl : prev.pnl;

          // ── Bail out entirely when nothing changed (zero re-render) ───────────
          if (
            newStatus       === prev.status &&
            newBalances     === prev.balances &&
            newOpportunities=== prev.opportunities &&
            newSummary      === prev.summary &&
            newPnl          === prev.pnl &&
            newLogs         === prev.logs &&
            newPositions    === prev.positions &&
            newTrades       === prev.trades &&
            wsTradesLoaded  === prev.tradesLoaded
          ) return prev;

          // Note: lastFetchedAt is intentionally NOT updated here.
          // It is only refreshed by the HTTP poll (fetchAll) so that
          // the Header "last updated" counter only counts HTTP round-trips.
          return {
            ...prev,
            status:        newStatus,
            balances:      newBalances,
            opportunities: newOpportunities,
            summary:       newSummary,
            pnl:           newPnl,
            logs:          newLogs,
            positions:     newPositions,
            trades:        newTrades,
            tradesLoaded:  wsTradesLoaded,
          };
        });
      } else if (msg.type === 'status_update') {
        setData(prev => {
          const sd = msg.data as BotStatus | undefined;
          if (!sd) return prev;
          if (
            sd.bot_running === prev.status.bot_running &&
            sd.active_positions === prev.status.active_positions
          ) return prev;
          return { ...prev, status: sd };
        });
      }
    });

    fetchAll();
    const interval = setInterval(fetchAll, 5000);

    return () => {
      clearInterval(interval);
      disconnectWebSocket();
    };
  }, [fetchAll]);

  return (
    <ErrorBoundary>
      <div className="App min-h-screen bg-slate-900">
        {/* RateBridge status beam — stretches full width at very top */}
        <div className={`status-beam ${data.status.bot_running ? 'status-beam--running' : 'status-beam--stopped'}`} />
        <Dashboard data={data} pnlHours={pnlHours} onPnlHoursChange={handlePnlHoursChange} />
      </div>
    </ErrorBoundary>
  );
}

export default App;
