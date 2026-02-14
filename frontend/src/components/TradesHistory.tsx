import React, { useState, useEffect } from 'react';
import { getTrades } from '../services/api';
import { Trade } from '../types';

const TradesHistory: React.FC = () => {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<number | undefined>(24);

  useEffect(() => {
    fetchTrades();
  }, [filter]);

  const fetchTrades = async () => {
    try {
      const data = await getTrades(100, filter);
      setTrades(data.trades || []);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching trades:', error);
      setLoading(false);
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

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  if (loading) {
    return (
      <div className="card text-center py-8">
        <div className="text-slate-400">Loading trades...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end space-x-2">
        <button
          onClick={() => setFilter(1)}
          className={`px-3 py-1 rounded ${
            filter === 1 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
          }`}
        >
          1h
        </button>
        <button
          onClick={() => setFilter(24)}
          className={`px-3 py-1 rounded ${
            filter === 24 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
          }`}
        >
          24h
        </button>
        <button
          onClick={() => setFilter(168)}
          className={`px-3 py-1 rounded ${
            filter === 168 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
          }`}
        >
          7d
        </button>
        <button
          onClick={() => setFilter(undefined)}
          className={`px-3 py-1 rounded ${
            filter === undefined ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
          }`}
        >
          All
        </button>
      </div>

      <div className="card overflow-x-auto">
        {trades.length === 0 ? (
          <div className="text-center py-8 text-slate-400">No trades found</div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-semibold">Symbol</th>
                <th className="text-left py-3 px-4 text-slate-400 font-semibold">Exchanges</th>
                <th className="text-left py-3 px-4 text-slate-400 font-semibold">Open Time</th>
                <th className="text-left py-3 px-4 text-slate-400 font-semibold">Close Time</th>
                <th className="text-right py-3 px-4 text-slate-400 font-semibold">Size</th>
                <th className="text-right py-3 px-4 text-slate-400 font-semibold">Entry Spread</th>
                <th className="text-right py-3 px-4 text-slate-400 font-semibold">Exit Spread</th>
                <th className="text-right py-3 px-4 text-slate-400 font-semibold">P&L</th>
                <th className="text-center py-3 px-4 text-slate-400 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <tr key={trade.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                  <td className="py-3 px-4 font-semibold">{trade.symbol}</td>
                  <td className="py-3 px-4 text-sm">
                    {trade.exchanges.long} / {trade.exchanges.short}
                  </td>
                  <td className="py-3 px-4 text-sm">{formatDate(trade.open_time)}</td>
                  <td className="py-3 px-4 text-sm">{formatDate(trade.close_time)}</td>
                  <td className="py-3 px-4 text-right">{trade.size}</td>
                  <td className="py-3 px-4 text-right text-sm">
                    {formatPercentage(trade.entry_spread)}
                  </td>
                  <td className="py-3 px-4 text-right text-sm">
                    {formatPercentage(trade.exit_spread)}
                  </td>
                  <td className={`py-3 px-4 text-right font-bold ${
                    trade.pnl >= 0 ? 'success-text' : 'danger-text'
                  }`}>
                    <div>{formatCurrency(trade.pnl)}</div>
                    <div className="text-sm">{formatPercentage(trade.pnl_percentage)}</div>
                  </td>
                  <td className="py-3 px-4 text-center">
                    <span className={`px-2 py-1 rounded text-xs ${
                      trade.status === 'closed' 
                        ? 'bg-green-500/20 text-green-400' 
                        : 'bg-yellow-500/20 text-yellow-400'
                    }`}>
                      {trade.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default TradesHistory;
