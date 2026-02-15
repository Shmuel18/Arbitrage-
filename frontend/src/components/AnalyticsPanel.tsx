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
    <div className="card p-5">
      <div className="flex justify-between items-center mb-3">
        <div className="card-header">{t.pnlChart}</div>
        <div className="flex gap-4 items-center">
          {(unrealized !== 0 || realized !== 0) && (
            <div className="text-xs mono" style={{ opacity: 0.7 }}>
              <span style={{ color: 'var(--green)' }}>R: {formatCurrency(realized)}</span>
              {' / '}
              <span style={{ color: unrealized >= 0 ? '#60a5fa' : 'var(--red)' }}>U: {formatCurrency(unrealized)}</span>
            </div>
          )}
          <div className="text-sm mono font-semibold" style={{ color: total >= 0 ? 'var(--green)' : 'var(--red)' }}>
            {formatCurrency(total)}
          </div>
        </div>
      </div>
      <div className="chart-area">
        {values.length > 1 ? (
          <svg width="100%" height="160" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            <path d={fillPath} fill={fillColor} />
            <path d={path} fill="none" stroke={strokeColor} strokeWidth="2" />
          </svg>
        ) : (
          <div className="text-muted text-xs">{t.waitingPnl}</div>
        )}
      </div>
    </div>
  );
};

export default AnalyticsPanel;
