import React from 'react';

interface LeftPanelProps {
  summary: { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number } | null;
  balances: { balances: Record<string, number>; total: number } | null;
}

const LeftPanel: React.FC<LeftPanelProps> = ({ summary, balances }) => {
  const totalBalance = balances?.total ?? 0;
  const tradeCount = summary?.total_trades ?? 0;
  const winRate = summary?.win_rate ?? 0;
  const activePos = summary?.active_positions ?? 0;
  const uptime = summary?.uptime_hours ?? 0;

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value);

  // Build per-exchange breakdown for risk chart
  const balEntries = balances?.balances ? Object.entries(balances.balances) : [];
  const riskData = balEntries.map(([name, val]) => ({
    name: name.toUpperCase(),
    value: totalBalance > 0 ? Math.round((val / totalBalance) * 100) : 0,
  }));

  return (
    <div className="space-y-4">
      {/* Total Portfolio Value */}
      <div className="border border-cyan-500/30 rounded-lg p-4 bg-slate-900/50">
        <div className="text-cyan-400 text-xs font-mono uppercase mb-2">Total Portfolio Value</div>
        <div className="text-3xl font-bold text-white mb-2">{formatCurrency(totalBalance)}</div>
        <div className="text-sm text-gray-400 font-mono">
          {balEntries.map(([name, val]) => (
            <span key={name} className="mr-3">{name.toUpperCase()}: {formatCurrency(val)}</span>
          ))}
        </div>
      </div>

      {/* Balance Distribution */}
      {riskData.length > 0 && (
        <div className="border border-cyan-500/30 rounded-lg p-4 bg-slate-900/50">
          <div className="text-cyan-400 text-xs font-mono uppercase mb-3">Balance Distribution</div>
          <div className="space-y-3">
            {riskData.map((item) => (
              <div key={item.name}>
                <div className="flex justify-between text-sm text-gray-300 mb-1">
                  <span className="text-xs">{item.name}</span>
                  <span className="text-cyan-400 font-mono">{item.value}%</span>
                </div>
                <div className="h-2 bg-slate-800 rounded overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-cyan-500 to-cyan-400" style={{ width: `${item.value}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats Grid */}
      <div className="border border-cyan-500/30 rounded-lg p-4 bg-slate-900/50">
        <div className="text-cyan-400 text-xs font-mono uppercase mb-3">Key Metrics</div>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-400">Total Trades</span>
            <span className="text-cyan-400 font-mono">{tradeCount}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Win Rate</span>
            <span className="text-green-400 font-mono">{(winRate * 100).toFixed(1)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Active Positions</span>
            <span className="text-cyan-400 font-mono">{activePos}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Uptime</span>
            <span className="text-cyan-400 font-mono">{uptime}h</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LeftPanel;
