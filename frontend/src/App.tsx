import React, { useState, useEffect, useCallback } from 'react';
import Dashboard from './components/Dashboard';
import { BotStatus } from './types';
import { connectWebSocket, disconnectWebSocket } from './services/websocket';
import { getOpportunities, getBalances, getLogs, getSummary, getPositions, getPnL, getTrades } from './services/api';
import './App.css';

export interface FullData {
  status: BotStatus;
  balances: { balances: Record<string, number>; total: number } | null;
  opportunities: { opportunities: any[]; count: number } | null;
  summary: { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number; all_time_pnl?: number; avg_pnl?: number } | null;
  pnl: { data_points: any[]; total_pnl: number } | null;
  logs: { timestamp: string; message: string; level: string }[];
  positions: any[];
  trades: any[];
  tradesLoaded: boolean;
  lastFetchedAt: number;
}

function App() {
  const [data, setData] = useState<FullData>({
    status: { bot_running: false, connected_exchanges: [], active_positions: 0, uptime: 0 },
    balances: null, opportunities: null, summary: null, pnl: null,
    logs: [], positions: [], trades: [], tradesLoaded: false,
    lastFetchedAt: Date.now(),
  });

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, balRes, oppRes, logsRes, summRes, posRes, pnlRes, tradesRes] = await Promise.allSettled([
        fetch('/api/status').then(r => r.json()),
        getBalances(),
        getOpportunities(),
        getLogs(50),
        getSummary(),
        getPositions(),
        getPnL(24),
        getTrades(10),
      ]);
      setData(prev => ({
        ...prev,
        status: statusRes.status === 'fulfilled' ? statusRes.value : prev.status,
        balances: balRes.status === 'fulfilled' ? balRes.value : prev.balances,
        opportunities: oppRes.status === 'fulfilled' ? oppRes.value : prev.opportunities,
        logs: logsRes.status === 'fulfilled' ? (logsRes.value.logs || []) : prev.logs,
        summary: summRes.status === 'fulfilled' && summRes.value?.total_trades != null ? summRes.value : prev.summary,
        positions: posRes.status === 'fulfilled' ? (posRes.value.positions || []) : prev.positions,
        pnl: pnlRes.status === 'fulfilled' ? pnlRes.value : prev.pnl,
        lastFetchedAt: Date.now(),
        tradesLoaded: true,
        trades: (() => {
          if (tradesRes.status !== 'fulfilled') return prev.trades;
          const newT = tradesRes.value.trades || [];
          if (newT.length === 0) return prev.trades;
          const prevIds = prev.trades.map((t: any) => t.id).join(',');
          const newIds = newT.map((t: any) => t.id).join(',');
          return prevIds === newIds ? prev.trades : newT;
        })(),
      }));
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []);

  useEffect(() => {
    connectWebSocket((msg) => {
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
              JSON.stringify(d.status.connected_exchanges) === JSON.stringify(prev.status.connected_exchanges)
            ) return prev.status;
            return d.status;
          })();

          // ── Balances: only swap when total changed ───────────────────────────
          const newBalances = (() => {
            if (!d.balances) return prev.balances;
            if (prev.balances && d.balances.total === prev.balances.total) return prev.balances;
            return d.balances;
          })();

          // ── Opportunities: only swap when the qualified set changes ──────────
          const newOpportunities = (() => {
            if (!d.opportunities) return prev.opportunities;
            const makeKey = (list: any[]) => list.map((o: any) => `${o.symbol}_${o.long_exchange}_${o.short_exchange}_${(o.immediate_spread_pct ?? 0).toFixed(4)}`).join('|');
            const prevKey = makeKey(prev.opportunities?.opportunities || []);
            const newKey  = makeKey(d.opportunities.opportunities || []);
            return prevKey === newKey ? prev.opportunities : d.opportunities;
          })();

          // ── Positions: only swap when set of IDs changes ─────────────────────
          const newPositions = (() => {
            if (!Array.isArray(d.positions)) return prev.positions;
            const prevKey = prev.positions.map((p: any) => p.id || p.symbol || '').join(',');
            const newKey  = d.positions.map((p: any) => p.id || p.symbol || '').join(',');
            return prevKey === newKey ? prev.positions : d.positions;
          })();

          // ── Trades: only swap when IDs change (prevents flicker) ─────────────
          const newTrades = (() => {
            const t = Array.isArray(d.trades) && d.trades.length > 0 ? d.trades : null;
            if (!t) return prev.trades;
            const prevIds = prev.trades.map((x: any) => x.id).join(',');
            const newIds  = t.map((x: any) => x.id).join(',');
            return prevIds === newIds ? prev.trades : t;
          })();

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
            newTrades       === prev.trades
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
          };
        });
      } else if (msg.type === 'status_update') {
        setData(prev => {
          if (!msg.data) return prev;
          if (
            msg.data.bot_running === prev.status.bot_running &&
            msg.data.active_positions === prev.status.active_positions
          ) return prev;
          return { ...prev, status: msg.data };
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
    <div className="App min-h-screen bg-slate-900">
      {/* RateBridge status beam — stretches full width at very top */}
      <div className={`status-beam ${data.status.bot_running ? 'status-beam--running' : 'status-beam--stopped'}`} />
      <Dashboard data={data} />
    </div>
  );
}

export default App;
