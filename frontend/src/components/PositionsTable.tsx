import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface PositionRow {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_qty: string;
  short_qty: string;
  entry_edge_pct: string;
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  state: string;
}

interface PositionsTableProps {
  positions: PositionRow[];
}

const PositionsTable: React.FC<PositionsTableProps> = ({ positions }) => {
  const { t } = useSettings();

  const formatFunding = (rate?: string | null) => {
    if (!rate) return '--';
    const n = Number(rate);
    if (Number.isNaN(n)) return '--';
    const pct = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const formatEdgePct = (val: string) => {
    const n = Number(val || 0);
    if (Number.isNaN(n)) return '--';
    return `${n.toFixed(4)}%`;
  };

  return (
    <div className="panel panel-strong">
      <div className="panel-header text-xs px-4 py-3 border-b border-cyan-500/20">
        {t.activePositions}
      </div>
      <div className="overflow-auto scrollbar-thin">
        <table className="neon-table w-full text-xs mono">
          <thead className="sticky top-0 bg-slate-900/80">
            <tr className="border-b border-cyan-500/10 text-gray-500">
              <th className="text-left py-2 px-3">{t.symbol}</th>
              <th className="text-left py-2 px-3">{t.longShort}</th>
              <th className="text-right py-2 px-3">{t.qtyLS}</th>
              <th className="text-right py-2 px-3">{t.entryFunding}</th>
              <th className="text-right py-2 px-3">{t.fundingLS}</th>
              <th className="text-right py-2 px-3">{t.state}</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-gray-500 py-6">{t.noOpenPositions}</td>
              </tr>
            ) : (
              positions.map((p) => (
                <tr key={p.id} className="border-b border-slate-800/40 hover:bg-slate-800/30">
                  <td className="py-2 px-3 text-cyan-300">{p.symbol}</td>
                  <td className="py-2 px-3 text-gray-300">
                    {p.long_exchange?.toUpperCase()} / {p.short_exchange?.toUpperCase()}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {p.long_qty} / {p.short_qty}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatEdgePct(p.entry_edge_pct)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatFunding(p.long_funding_rate)} / {formatFunding(p.short_funding_rate)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-400">{p.state}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default PositionsTable;
