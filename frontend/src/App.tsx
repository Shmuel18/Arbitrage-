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
  lastFetchedAt: number;
}

function App() {
  const [data, setData] = useState<FullData>({
    status: { bot_running: false, connected_exchanges: [], active_positions: 0, uptime: 0 },
    balances: null, opportunities: null, summary: null, pnl: null,
    logs: [], positions: [], trades: [],
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
        trades: tradesRes.status === 'fulfilled' ? (tradesRes.value.trades || []) : prev.trades,
      }));
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []);

  useEffect(() => {
    connectWebSocket((msg) => {
      if (msg.type === 'full_update' && msg.data) {
        const d = msg.data;
        // Only accept summary from WS if it has computed fields (all_time_pnl)
        // to avoid overwriting accurate HTTP data with raw Redis defaults
        const wsSummary = d.summary && d.summary.all_time_pnl !== undefined ? d.summary : null;
        setData(prev => ({
          status: d.status || prev.status,
          balances: d.balances || prev.balances,
          opportunities: d.opportunities || prev.opportunities,
          summary: wsSummary || prev.summary,
          // Only accept pnl from WS if it has the full structure with actual data points
          pnl: (d.pnl && Array.isArray(d.pnl.data_points) && d.pnl.data_points.length > 0) ? d.pnl : prev.pnl,
          logs: d.logs || prev.logs,
          positions: d.positions || prev.positions,
          trades: prev.trades,
          lastFetchedAt: Date.now(),
        }));
      } else if (msg.type === 'status_update') {
        setData(prev => ({ ...prev, status: msg.data }));
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
