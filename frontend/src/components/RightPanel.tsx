import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface Opportunity {
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_rate: number;
  short_rate: number;
  net_pct: number;
  gross_pct: number;
  funding_spread_pct?: number;
  price: number;
  mode: string;
}

interface RightPanelProps {
  opportunities: { opportunities: Opportunity[]; count: number } | null;
}

const RightPanel: React.FC<RightPanelProps> = ({ opportunities }) => {
  const { t } = useSettings();
  const opps = opportunities?.opportunities ?? [];
  const count = opportunities?.count ?? 0;

  const formatFunding = (rate: number) => {
    // Raw rates are decimals (e.g. 0.003 = 0.3%), multiply by 100
    const pct = Math.abs(rate) <= 1 ? rate * 100 : rate;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const formatSpread = (pct: number) => {
    // funding_spread_pct is already in % (e.g. 0.46 = 0.46%)
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const getRateStyle = (rate: number): React.CSSProperties => {
    if (rate > 0) return { color: 'var(--green)' };
    if (rate < 0) return { color: 'var(--red)' };
    return { color: 'var(--text-muted)' };
  };

  return (
    <div className="card flex flex-col">
      <div className="card-header px-5 py-4 border-b" style={{ borderColor: 'var(--card-border)' }}>
        {t.liveOpportunities} <span className="card-header-muted">({count})</span>
      </div>

      <div className="flex-1 overflow-auto scrollbar-thin">
        {opps.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-muted text-sm mono">
            {t.scanning}
          </div>
        ) : (
          <table className="corp-table">
            <thead>
              <tr>
                <th>{t.pair}</th>
                <th>{t.long}</th>
                <th>{t.short}</th>
                <th className="text-end">{t.fundingL}</th>
                <th className="text-end">{t.fundingS}</th>
                <th className="text-end">{t.fundingSpread}</th>
              </tr>
            </thead>
            <tbody>
              {opps.map((opp, i) => (
                <tr key={i}>
                  <td className="font-semibold text-accent">{opp.symbol}</td>
                  <td>{opp.long_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td>{opp.short_exchange?.toUpperCase().slice(0, 3)}</td>
                  <td className="text-end mono" style={getRateStyle(opp.long_rate)}>
                    {formatFunding(opp.long_rate)}
                  </td>
                  <td className="text-end mono" style={getRateStyle(opp.short_rate)}>
                    {formatFunding(opp.short_rate)}
                  </td>
                  <td className="text-end mono font-semibold" style={getRateStyle(opp.funding_spread_pct ?? (opp.short_rate - opp.long_rate))}>
                    {formatSpread(opp.funding_spread_pct ?? (opp.short_rate - opp.long_rate))}
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
