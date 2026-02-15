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
    <div className="card">
      <div className="card-header px-5 py-4 border-b" style={{ borderColor: 'var(--card-border)' }}>
        {t.activePositions}
      </div>
      <div className="overflow-auto scrollbar-thin">
        <table className="corp-table">
          <thead>
            <tr>
              <th>{t.symbol}</th>
              <th>{t.longShort}</th>
              <th className="text-end">{t.qtyLS}</th>
              <th className="text-end">{t.entryFunding}</th>
              <th className="text-end">{t.fundingLS}</th>
              <th className="text-end">{t.state}</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-secondary py-8">{t.noOpenPositions}</td>
              </tr>
            ) : (
              positions.map((p) => (
                <tr key={p.id}>
                  <td className="font-semibold text-accent">{p.symbol}</td>
                  <td>{p.long_exchange?.toUpperCase()} / {p.short_exchange?.toUpperCase()}</td>
                  <td className="text-end mono">{p.long_qty} / {p.short_qty}</td>
                  <td className="text-end mono">{formatEdgePct(p.entry_edge_pct)}</td>
                  <td className="text-end mono">{formatFunding(p.long_funding_rate)} / {formatFunding(p.short_funding_rate)}</td>
                  <td className="text-end text-secondary">{p.state}</td>
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
