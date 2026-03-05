import React from 'react';
import { useSettings } from '../context/SettingsContext';
import { formatCurrency } from '../utils/format';

interface PnlPoint {
  pnl: number;
  cumulative_pnl: number;
  unrealized?: number;
  realized?: number;
  timestamp: number;
}

interface AnalyticsPanelProps {
  pnl: { data_points: PnlPoint[]; total_pnl: number; unrealized_pnl?: number; realized_pnl?: number } | null;
  pnlHours: number;
  onPnlHoursChange: (hours: number) => void;
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ pnl, pnlHours, onPnlHoursChange }) => {
  const { t } = useSettings();
  const points = pnl?.data_points ?? [];
  const total = pnl?.total_pnl ?? 0;
  const unrealized = pnl?.unrealized_pnl ?? 0;
  const realized = pnl?.realized_pnl ?? 0;

  const width = 600;
  const height = 160;

  // Always prepend a zero-baseline point so even a single closed trade renders a line
  const chartPoints: PnlPoint[] = points.length > 0
    ? [{ pnl: 0, cumulative_pnl: 0, timestamp: points[0].timestamp - 60 }, ...points]
    : points;

  const values = chartPoints.map((p) => p.cumulative_pnl);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;

  const scaleX = (i: number) => (values.length <= 1 ? 0 : (i / (values.length - 1)) * width);
  const scaleY = (v: number) => height - ((v - min) / (max - min || 1)) * height;

  const path = values.length
    ? values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i)} ${scaleY(v)}`).join(' ')
    : '';

  // Zero baseline in Y coordinates (clamped to chart bounds)
  const zeroY = Math.max(0, Math.min(height, scaleY(0)));
  const accentColor = total >= 0 ? '#22c55e' : '#ef4444'; // for header accent only

  const closedFillPath = values.length > 1
    ? `${path} L ${scaleX(values.length - 1)} ${zeroY} L ${scaleX(0)} ${zeroY} Z`
    : '';

  // Stroke: gradient that switches color at zero line
  const zeroFrac = zeroY / height; // 0 = top, 1 = bottom

  return (
    <div className="card p-5" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg, transparent, ${accentColor}66, transparent)`,
        borderRadius: '14px 14px 0 0',
      }} />

      <div className="nx-analytics-header">
        <div className="nx-section-header">
          <div className="nx-section-header__icon" style={{ background: `${accentColor}14`, borderColor: `${accentColor}22` }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={accentColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>
            </svg>
          </div>
          {t.pnlChart}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {(unrealized !== 0 || realized !== 0) && (
            <div className="nx-analytics-breakdown">
              <span>
                <span className="nx-analytics-breakdown__dot" style={{ background: 'var(--green)' }} />
                R {formatCurrency(realized)}
              </span>
              <span>
                <span className="nx-analytics-breakdown__dot" style={{ background: unrealized >= 0 ? '#60a5fa' : 'var(--red)' }} />
                U {formatCurrency(unrealized)}
              </span>
            </div>
          )}
          <div className={`nx-analytics-total ${total >= 0 ? 'nx-analytics-total--positive' : 'nx-analytics-total--negative'}`}>
            {formatCurrency(total)}
          </div>
        </div>
      </div>

      <div className="flex justify-end mb-4">
        <div className="nx-time-pills">
          {[
            { label: '24h', value: 24 },
            { label: '7d', value: 168 },
            { label: '30d', value: 720 },
            { label: '90d', value: 2160 },
            { label: '180d', value: 4320 },
          ].map((btn) => (
            <button
              key={btn.value}
              className={`nx-time-btn ${pnlHours === btn.value ? 'nx-time-btn--active' : ''}`}
              onClick={() => onPnlHoursChange(btn.value)}
            >
              {btn.label}
            </button>
          ))}
        </div>
      </div>

      <div className="nx-chart-area">
        {values.length > 1 ? (
          <>
            <svg width="100%" height="160" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
              <defs>
                {/* Clip to above-zero region (green area) */}
                <clipPath id="clip-above">
                  <rect x="0" y="0" width={width} height={zeroY} />
                </clipPath>
                {/* Clip to below-zero region (red area) */}
                <clipPath id="clip-below">
                  <rect x="0" y={zeroY} width={width} height={height - zeroY} />
                </clipPath>
                {/* Line gradient: green above zero, red below */}
                <linearGradient id="line-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={`${zeroFrac * 100}%`} stopColor="#22c55e" />
                  <stop offset={`${zeroFrac * 100}%`} stopColor="#ef4444" />
                </linearGradient>
              </defs>
              {/* Zero baseline */}
              <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="rgba(255,255,255,0.15)" strokeWidth="1" strokeDasharray="4 4" />
              {/* Green fill — above zero */}
              <path d={closedFillPath} fill="rgba(34,197,94,0.15)" clipPath="url(#clip-above)" />
              {/* Red fill — below zero */}
              <path d={closedFillPath} fill="rgba(239,68,68,0.15)" clipPath="url(#clip-below)" />
              {/* Line with split color */}
              <path d={path} fill="none" stroke="url(#line-grad)" strokeWidth="2" filter="drop-shadow(0 0 3px rgba(255,255,255,0.2))" />
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
