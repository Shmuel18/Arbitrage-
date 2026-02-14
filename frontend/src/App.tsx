import React, { useState, useEffect, useCallback } from 'react';
import Dashboard from './components/Dashboard';
import { BotStatus } from './types';
import { connectWebSocket, disconnectWebSocket } from './services/websocket';
import { getOpportunities, getBalances, getLogs, getSummary } from './services/api';
import './App.css';

export interface FullData {
  status: BotStatus;
  balances: { balances: Record<string, number>; total: number } | null;
  opportunities: { opportunities: any[]; count: number } | null;
  summary: { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number } | null;
  logs: { timestamp: string; message: string; level: string }[];
  positions: any[];
}

function App() {
  const [data, setData] = useState<FullData>({
    status: { bot_running: false, connected_exchanges: [], active_positions: 0, uptime: 0 },
    balances: null,
    opportunities: null,
    summary: null,
    logs: [],
    positions: [],
  });

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, balRes, oppRes, logsRes, summRes] = await Promise.allSettled([
        fetch('http://localhost:8000/api/status').then(r => r.json()),
        getBalances(),
        getOpportunities(),
        getLogs(50),
        getSummary(),
      ]);
      setData(prev => ({
        ...prev,
        status: statusRes.status === 'fulfilled' ? statusRes.value : prev.status,
        balances: balRes.status === 'fulfilled' ? balRes.value : prev.balances,
        opportunities: oppRes.status === 'fulfilled' ? oppRes.value : prev.opportunities,
        logs: logsRes.status === 'fulfilled' ? (logsRes.value.logs || []) : prev.logs,
        summary: summRes.status === 'fulfilled' ? summRes.value : prev.summary,
      }));
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }, []);

  useEffect(() => {
    connectWebSocket((msg) => {
      if (msg.type === 'full_update' && msg.data) {
        const d = msg.data;
        setData(prev => ({
          status: d.status || prev.status,
          balances: d.balances || prev.balances,
          opportunities: d.opportunities || prev.opportunities,
          summary: d.summary || prev.summary,
          logs: d.logs || prev.logs,
          positions: d.positions || prev.positions,
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
      <Dashboard data={data} />
    </div>
  );
}

export default App;
