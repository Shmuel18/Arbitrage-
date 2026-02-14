import React from 'react';

interface CenterPanelProps {
  exchanges: string[];
  balances: { balances: Record<string, number>; total: number } | null;
  summary: { total_pnl: number; total_trades: number; win_rate: number; active_positions: number; uptime_hours: number } | null;
}

const CenterPanel: React.FC<CenterPanelProps> = ({ exchanges, balances, summary }) => {
  const formatExchange = (exchange: string) => exchange.toUpperCase().substring(0, 2);

  const exchangesList = exchanges.length > 0 ? exchanges : [];
  const exchangeColors: Record<string, string> = {
    binance: 'bg-yellow-500',
    bybit: 'bg-blue-500',
    okx: 'bg-green-500',
    gateio: 'bg-purple-500',
  };
  const exchangeStroke: Record<string, string> = {
    binance: 'rgb(234, 179, 8)',
    bybit: 'rgb(59, 130, 246)',
    okx: 'rgb(34, 197, 94)',
    gateio: 'rgb(168, 85, 247)',
  };

  const totalBalance = balances?.total ?? 0;
  const activeArb = summary?.active_positions ?? 0;
  const pairsScanned = summary ? '301' : '—';

  const formatCurrency = (val: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(val);

  // Per-exchange balance labels
  const balMap = balances?.balances ?? {};

  return (
    <div className="border border-cyan-500/30 rounded-lg p-6 bg-slate-900/50 h-full flex flex-col">
      <div className="text-cyan-400 text-xs font-mono uppercase mb-6">Neural Process Core</div>

      {/* Total Balance */}
      <div className="text-center mb-4">
        <div className="text-gray-400 text-xs">TOTAL BALANCE</div>
        <div className="text-2xl font-bold text-cyan-400 font-mono">{formatCurrency(totalBalance)}</div>
      </div>

      {/* Network Diagram */}
      <div className="flex-1 flex items-center justify-center relative mb-8">
        <svg className="w-full h-64" viewBox="0 0 300 300">
          <polygon points="150,20 280,260 20,260" fill="none" stroke="rgba(34,211,238,0.2)" strokeWidth="2" strokeDasharray="5,5" />
          <circle cx="150" cy="140" r="40" fill="rgba(34,211,238,0.1)" stroke="rgb(34,211,238)" strokeWidth="2" />
          <text x="150" y="155" textAnchor="middle" fill="#06b6d4" fontSize="24">⚡</text>

          {exchangesList[0] && (
            <>
              <circle cx="50" cy="50" r="30" fill={exchangeColors[exchangesList[0].toLowerCase()] || 'rgb(34,211,238)'} opacity="0.3" />
              <circle cx="50" cy="50" r="30" fill="none" stroke={exchangeStroke[exchangesList[0].toLowerCase()] || 'rgb(34,211,238)'} strokeWidth="2" />
              <text x="50" y="55" textAnchor="middle" fill="white" fontSize="12" fontWeight="bold">{formatExchange(exchangesList[0])}</text>
              <text x="50" y="95" textAnchor="middle" fill="#94a3b8" fontSize="9">{formatCurrency(balMap[exchangesList[0].toLowerCase()] ?? 0)}</text>
              <line x1="150" y1="140" x2="50" y2="50" stroke="rgba(34,211,238,0.4)" strokeWidth="1" strokeDasharray="3,3" />
            </>
          )}
          {exchangesList[1] && (
            <>
              <circle cx="250" cy="50" r="30" fill={exchangeColors[exchangesList[1].toLowerCase()] || 'rgb(34,211,238)'} opacity="0.3" />
              <circle cx="250" cy="50" r="30" fill="none" stroke={exchangeStroke[exchangesList[1].toLowerCase()] || 'rgb(34,211,238)'} strokeWidth="2" />
              <text x="250" y="55" textAnchor="middle" fill="white" fontSize="12" fontWeight="bold">{formatExchange(exchangesList[1])}</text>
              <text x="250" y="95" textAnchor="middle" fill="#94a3b8" fontSize="9">{formatCurrency(balMap[exchangesList[1].toLowerCase()] ?? 0)}</text>
              <line x1="150" y1="140" x2="250" y2="50" stroke="rgba(34,211,238,0.4)" strokeWidth="1" strokeDasharray="3,3" />
            </>
          )}
          {exchangesList[2] && (
            <>
              <circle cx="150" cy="260" r="30" fill={exchangeColors[exchangesList[2].toLowerCase()] || 'rgb(34,211,238)'} opacity="0.3" />
              <circle cx="150" cy="260" r="30" fill="none" stroke={exchangeStroke[exchangesList[2].toLowerCase()] || 'rgb(34,211,238)'} strokeWidth="2" />
              <text x="150" y="265" textAnchor="middle" fill="white" fontSize="12" fontWeight="bold">{formatExchange(exchangesList[2])}</text>
              <text x="150" y="290" textAnchor="middle" fill="#94a3b8" fontSize="9">{formatCurrency(balMap[exchangesList[2].toLowerCase()] ?? 0)}</text>
              <line x1="150" y1="140" x2="150" y2="260" stroke="rgba(34,211,238,0.4)" strokeWidth="1" strokeDasharray="3,3" />
            </>
          )}
        </svg>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 gap-4">
        <div className="border border-cyan-500/20 rounded p-3 bg-slate-800/30">
          <div className="text-xs text-gray-400 mb-1">ACTIVE POSITIONS</div>
          <div className="text-2xl font-bold text-cyan-400">{activeArb}</div>
        </div>
        <div className="border border-cyan-500/20 rounded p-3 bg-slate-800/30">
          <div className="text-xs text-gray-400 mb-1">EXCHANGES</div>
          <div className="text-2xl font-bold text-green-400">{exchangesList.length}</div>
        </div>
        <div className="border border-cyan-500/20 rounded p-3 bg-slate-800/30 col-span-2">
          <div className="text-xs text-gray-400 mb-1">PAIRS SCANNED</div>
          <div className="flex justify-between items-center">
            <div className="text-xl font-bold text-cyan-400">{pairsScanned}</div>
            <div className="text-xs text-gray-500">common symbols across exchanges</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CenterPanel;
