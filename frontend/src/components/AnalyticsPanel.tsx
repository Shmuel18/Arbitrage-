import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface PnlPoint {
  pnl: number;
  cumulative_pnl: number;
  unrealized?: number;
  realized?: number;
  timestamp: number;
}

interface AnalyticsPanelProps {
  pnl: { data_points: PnlPoint[]; total_pnl: number; unrealized_pnl?: number; realized_pnl?: number } | null;
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ pnl }) => {
  const { t, theme } = useSettings();
  const points = pnl?.data_points ?? [];
  const total = pnl?.total_pnl ?? 0;
  const unrealized = pnl?.unrealized_pnl ?? 0;
  const realized = pnl?.realized_pnl ?? 0;

  const width = 600;
  const height = 160;

  const values = points.map((p) => p.cumulative_pnl);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;

  const scaleX = (i: number) => (values.length <= 1 ? 0 : (i / (values.length - 1)) * width);
  const scaleY = (v: number) => height - ((v - min) / (max - min || 1)) * height;

  const path = values.length
    ? values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i)} ${scaleY(v)}`).join(' ')
    : '';

  const strokeColor = total >= 0 ? '#22c55e' : '#ef4444';
  const fillPath = values.length > 1
    ? `${path} L ${scaleX(values.length - 1)} ${height} L ${scaleX(0)} ${height} Z`
    : '';
  const fillColor = total >= 0 ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)';

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value);

  return (
    <div className="card p-5" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg, transparent, ${strokeColor}66, transparent)`,
        borderRadius: '14px 14px 0 0',
      }} />

      <div className="flex justify-between items-center mb-4">
        <div className="card-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={strokeColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.8 }}>
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>
          </svg>
          {t.pnlChart}
        </div>
        <div className="flex gap-5 items-center">
          {(unrealized !== 0 || realized !== 0) && (
            <div className="mono" style={{ fontSize: 11, opacity: 0.7, display: 'flex', gap: 10 }}>
              <span style={{ color: 'var(--green)' }}>R {formatCurrency(realized)}</span>
              <span style={{ color: unrealized >= 0 ? '#60a5fa' : 'var(--red)' }}>U {formatCurrency(unrealized)}</span>
            </div>
          )}
          <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: total >= 0 ? 'var(--green)' : 'var(--red)', letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums' }}>
            {formatCurrency(total)}
          </div>
        </div>
      </div>

      <div className="chart-area" style={{ position: 'relative', overflow: 'hidden' }}>
        {values.length > 1 ? (
          <>
            <svg width="100%" height="160" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
              <defs>
                <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={strokeColor} stopOpacity="0.2"/>
                  <stop offset="100%" stopColor={strokeColor} stopOpacity="0"/>
                </linearGradient>
              </defs>
              <path d={fillPath} fill="url(#chart-fill)" />
              <path d={path} fill="none" stroke={strokeColor} strokeWidth="2" filter={`drop-shadow(0 0 4px ${strokeColor}88)`} />
            </svg>
          </>
        ) : (
          <div className="text-muted text-xs" style={{ padding: '40px 0', textAlign: 'center' }}>{t.waitingPnl}</div>
        )}
      </div>
    </div>
  );
};

export default AnalyticsPanel;
