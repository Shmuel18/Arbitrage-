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
  immediate_spread_pct?: number;
  hourly_rate_pct?: number;
  min_interval_hours?: number;
  next_funding_ms?: number | null;
  qualified?: boolean;
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

  const formatCountdown = (ms: number | null | undefined): string => {
    if (!ms) return '—';
    const now = Date.now();
    const diff = ms - now;
    if (diff <= 0) return 'NOW';
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    const remainMins = mins % 60;
    return `${hrs}h${remainMins > 0 ? remainMins + 'm' : ''}`;
  };

  const MIN_SPREAD_THRESHOLD = 0.5; // must match backend min_immediate_spread

  const getRateStyle = (rate: number): React.CSSProperties => {
    if (rate > 0) return { color: 'var(--green)' };
    if (rate < 0) return { color: 'var(--red)' };
    return { color: 'var(--text-muted)' };
  };

  const aboveThreshold = opps.filter(o => o.qualified !== false);
  const belowThreshold = opps.filter(o => o.qualified === false);

  const renderRow = (opp: Opportunity, i: number, dimmed: boolean) => {
    const immediateSpread = opp.immediate_spread_pct ?? 0;
    const hourlyRate = opp.hourly_rate_pct ?? 0;
    const interval = opp.min_interval_hours ?? 8;
    const countdown = formatCountdown(opp.next_funding_ms);
    const isUrgent = opp.next_funding_ms && (opp.next_funding_ms - Date.now()) < 900000; // < 15 min
    const rowStyle: React.CSSProperties = dimmed ? { opacity: 0.45 } : {};
    return (
      <tr key={i} style={rowStyle}>
        <td>
          {!dimmed && <span style={{ color: 'var(--green)', marginInlineEnd: 6, fontSize: 10 }}>●</span>}
          {dimmed && <span style={{ color: 'var(--text-muted)', marginInlineEnd: 6, fontSize: 10 }}>○</span>}
          <span className="font-semibold text-accent">{opp.symbol}</span>
        </td>
        <td>{opp.long_exchange?.toUpperCase().slice(0, 3)}</td>
        <td>{opp.short_exchange?.toUpperCase().slice(0, 3)}</td>
        <td className="text-end mono" style={getRateStyle(opp.long_rate ?? 0)}>
          {formatFunding(opp.long_rate ?? 0)}
        </td>
        <td className="text-end mono" style={getRateStyle(opp.short_rate ?? 0)}>
          {formatFunding(opp.short_rate ?? 0)}
        </td>
        <td className="text-end mono font-semibold" style={getRateStyle(immediateSpread)}>
          {formatSpread(immediateSpread)}
        </td>
        <td className="text-end mono font-semibold" style={getRateStyle(hourlyRate)}>
          {formatSpread(hourlyRate)}
        </td>
        <td className="text-end mono" style={{
          color: isUrgent ? 'var(--green)' : 'var(--text-muted)',
          fontWeight: isUrgent ? 600 : 400,
          fontSize: 12
        }}>
          ⏱ {countdown}
        </td>
      </tr>
    );
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
                <th className="text-end">{t.immediateSpreadOpp}</th>
                <th className="text-end">{t.hourlyRate}</th>
                <th className="text-end">{t.countdown}</th>
              </tr>
            </thead>
            <tbody>
              {aboveThreshold.map((opp, i) => renderRow(opp, i, false))}
              {aboveThreshold.length > 0 && belowThreshold.length > 0 && (
                <tr>
                  <td colSpan={8} style={{ padding: '4px 16px', fontSize: 11, color: 'var(--text-muted)', borderBottom: '1px solid var(--card-border)' }}>
                    ── {t.belowThreshold} ──
                  </td>
                </tr>
              )}
              {belowThreshold.map((opp, i) => renderRow(opp, i + aboveThreshold.length, true))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default RightPanel;
