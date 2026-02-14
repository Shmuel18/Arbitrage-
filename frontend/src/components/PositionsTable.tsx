import React, { useState, useEffect } from 'react';
import { getPositions, closePosition } from '../services/api';
import { Position } from '../types';

const PositionsTable: React.FC = () => {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPositions();
    const interval = setInterval(fetchPositions, 3000);
    return () => clearInterval(interval);
  }, []);

  const fetchPositions = async () => {
    try {
      const data = await getPositions();
      setPositions(data.positions || []);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching positions:', error);
      setLoading(false);
    }
  };

  const handleClosePosition = async (positionId: string) => {
    if (window.confirm('Are you sure you want to close this position?')) {
      try {
        await closePosition(positionId);
        alert('Close command sent!');
        fetchPositions();
      } catch (error) {
        console.error('Error closing position:', error);
        alert('Failed to close position');
      }
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

  if (loading) {
    return (
      <div className="card text-center py-8">
        <div className="text-slate-400">Loading positions...</div>
      </div>
    );
  }

  if (positions.length === 0) {
    return (
      <div className="card text-center py-8">
        <div className="text-slate-400">No active positions</div>
      </div>
    );
  }

  return (
    <div className="card overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-slate-700">
            <th className="text-left py-3 px-4 text-slate-400 font-semibold">Symbol</th>
            <th className="text-left py-3 px-4 text-slate-400 font-semibold">Exchanges</th>
            <th className="text-right py-3 px-4 text-slate-400 font-semibold">Size</th>
            <th className="text-right py-3 px-4 text-slate-400 font-semibold">Entry Price</th>
            <th className="text-right py-3 px-4 text-slate-400 font-semibold">Current Price</th>
            <th className="text-right py-3 px-4 text-slate-400 font-semibold">P&L</th>
            <th className="text-right py-3 px-4 text-slate-400 font-semibold">Funding</th>
            <th className="text-center py-3 px-4 text-slate-400 font-semibold">Actions</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
              <td className="py-3 px-4 font-semibold">{position.symbol}</td>
              <td className="py-3 px-4">
                <div className="text-sm">
                  <div className="text-green-400">↗ {position.exchanges.long}</div>
                  <div className="text-red-400">↘ {position.exchanges.short}</div>
                </div>
              </td>
              <td className="py-3 px-4 text-right">{position.size}</td>
              <td className="py-3 px-4 text-right">
                <div className="text-sm">
                  <div>{formatCurrency(position.entry_price.long)}</div>
                  <div>{formatCurrency(position.entry_price.short)}</div>
                </div>
              </td>
              <td className="py-3 px-4 text-right">
                <div className="text-sm">
                  <div>{formatCurrency(position.current_price.long)}</div>
                  <div>{formatCurrency(position.current_price.short)}</div>
                </div>
              </td>
              <td className={`py-3 px-4 text-right font-bold ${
                position.pnl >= 0 ? 'success-text' : 'danger-text'
              }`}>
                <div>{formatCurrency(position.pnl)}</div>
                <div className="text-sm">{formatPercentage(position.pnl_percentage)}</div>
              </td>
              <td className="py-3 px-4 text-right text-sm">
                {formatPercentage(position.funding_rate)}
              </td>
              <td className="py-3 px-4 text-center">
                <button
                  onClick={() => handleClosePosition(position.id)}
                  className="px-3 py-1 bg-red-600 hover:bg-red-700 text-white text-sm rounded transition-colors"
                >
                  Close
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default PositionsTable;
