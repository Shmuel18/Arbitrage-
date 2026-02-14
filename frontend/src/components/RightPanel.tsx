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

  const getBpsColor = (bps: number) => {
    if (bps > 5) return 'text-green-400';
    if (bps > 0) return 'text-cyan-400';
    return 'text-red-400';
  };

  return (
    <div className="border border-cyan-500/30 rounded-lg p-4 bg-slate-900/50 h-full flex flex-col">
      <div className="text-cyan-400 text-xs font-mono uppercase mb-3 pb-2 border-b border-cyan-500/20">
        Live Scanner ({count} opportunities)
      </div>

      <div className="flex-1 overflow-auto">
        {opps.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm font-mono">
            Scanning for opportunities...
          </div>
        ) : (
          <table className="w-full text-xs font-mono">
            <thead className="sticky top-0">
              <tr className="border-b border-cyan-500/10">
                <th className="text-left text-gray-500 py-1 px-1">PAIR</th>
                <th className="text-left text-gray-500 py-1 px-1">LONG</th>
                <th className="text-left text-gray-500 py-1 px-1">SHORT</th>
                <th className="text-right text-gray-500 py-1 px-1">NET BPS</th>
                <th className="text-right text-gray-500 py-1 px-1">PRICE</th>
              </tr>
            </thead>
            <tbody>
              {opps.map((opp, i) => (
                <tr key={i} className="border-b border-slate-800/30 hover:bg-slate-800/20 transition">
                  <td className="py-2 px-1 text-cyan-400">{opp.symbol}</td>
                  <td className="py-2 px-1 text-gray-300">{opp.long_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td className="py-2 px-1 text-gray-300">{opp.short_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td className={`py-2 px-1 text-right font-semibold ${getBpsColor(opp.net_bps)}`}>
                    {opp.net_bps > 0 ? '+' : ''}{opp.net_bps?.toFixed(2)}
                  </td>
                  <td className="py-2 px-1 text-right text-gray-400">
                    ${opp.price?.toLocaleString(undefined, { maximumFractionDigits: 4 })}
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
