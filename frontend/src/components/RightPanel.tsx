import React from 'react';

interface Opportunity {
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_rate: number;
  short_rate: number;
  net_bps: number;
  gross_bps: number;
  price: number;
  mode: string;
}

interface RightPanelProps {
  opportunities: { opportunities: Opportunity[]; count: number } | null;
}

const RightPanel: React.FC<RightPanelProps> = ({ opportunities }) => {
  const opps = opportunities?.opportunities ?? [];
  const count = opportunities?.count ?? 0;

  const formatFunding = (rate: number) => {
    const pct = Math.abs(rate) <= 1 ? rate * 100 : rate;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const getRateColor = (rate: number) => {
    if (rate > 0) return 'text-green-400';
    if (rate < 0) return 'text-red-400';
    return 'text-gray-400';
  };

  return (
    <div className="panel panel-strong p-4 h-full flex flex-col">
      <div className="panel-header text-xs mb-3 pb-2 border-b border-cyan-500/20">
        Live Opportunities ({count})
      </div>

      <div className="flex-1 overflow-auto scrollbar-thin">
        {opps.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm font-mono">
            Scanning for opportunities...
          </div>
        ) : (
          <table className="neon-table w-full text-xs mono">
            <thead className="sticky top-0 bg-slate-900/80">
              <tr className="border-b border-cyan-500/10">
                <th className="text-left text-gray-500 py-1 px-1">PAIR</th>
                <th className="text-left text-gray-500 py-1 px-1">LONG</th>
                <th className="text-left text-gray-500 py-1 px-1">SHORT</th>
                <th className="text-right text-gray-500 py-1 px-1">FUNDING L</th>
                <th className="text-right text-gray-500 py-1 px-1">FUNDING S</th>
                <th className="text-right text-gray-500 py-1 px-1">NET %</th>
              </tr>
            </thead>
            <tbody>
              {opps.map((opp, i) => (
                <tr key={i} className="border-b border-slate-800/30 hover:bg-slate-800/20 transition">
                  <td className="py-2 px-1 text-cyan-400">{opp.symbol}</td>
                  <td className="py-2 px-1 text-gray-300">{opp.long_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td className="py-2 px-1 text-gray-300">{opp.short_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td className={`py-2 px-1 text-right ${getRateColor(opp.long_rate)}`}>
                    {formatFunding(opp.long_rate)}
                  </td>
                  <td className={`py-2 px-1 text-right ${getRateColor(opp.short_rate)}`}>
                    {formatFunding(opp.short_rate)}
                  </td>
                  <td className={`py-2 px-1 text-right font-semibold ${getRateColor(opp.short_rate - opp.long_rate)}`}>
                    {formatFunding(opp.short_rate - opp.long_rate)}
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

export default RightPanel;
