import React, { useState, useEffect } from 'react';
import { getSummary } from '../services/api';
import { Summary } from '../types';

const StatsCards: React.FC = () => {
  const [summary, setSummary] = useState<Summary>({
    total_pnl: 0,
    total_trades: 0,
    win_rate: 0,
    active_positions: 0,
    uptime_hours: 0,
  });

  useEffect(() => {
    fetchSummary();
    const interval = setInterval(fetchSummary, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchSummary = async () => {
    try {
      const data = await getSummary();
      setSummary(data);
    } catch (error) {
      console.error('Error fetching summary:', error);
    }
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const formatPercentage = (value: number) => {
    return `${(value * 100).toFixed(2)}%`;
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">
      {/* Total P&L */}
      <div className="stat-card card">
        <div className="text-sm text-slate-400 mb-1">Total P&L</div>
        <div className={`text-3xl font-bold ${
          summary.total_pnl >= 0 ? 'success-text' : 'danger-text'
        }`}>
          {formatCurrency(summary.total_pnl)}
        </div>
      </div>

      {/* Total Trades */}
      <div className="stat-card card">
        <div className="text-sm text-slate-400 mb-1">Total Trades</div>
        <div className="text-3xl font-bold text-white">
          {summary.total_trades}
        </div>
      </div>

      {/* Win Rate */}
      <div className="stat-card card">
        <div className="text-sm text-slate-400 mb-1">Win Rate</div>
        <div className="text-3xl font-bold text-purple-400">
          {formatPercentage(summary.win_rate)}
        </div>
      </div>

      {/* Active Positions */}
      <div className="stat-card card">
        <div className="text-sm text-slate-400 mb-1">Active Positions</div>
        <div className="text-3xl font-bold text-blue-400">
          {summary.active_positions}
        </div>
      </div>

      {/* Uptime */}
      <div className="stat-card card">
        <div className="text-sm text-slate-400 mb-1">Uptime</div>
        <div className="text-3xl font-bold text-green-400">
          {summary.uptime_hours.toFixed(1)}h
        </div>
      </div>
    </div>
  );
};

export default StatsCards;
