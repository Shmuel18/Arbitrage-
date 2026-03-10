import React, { useState } from 'react';
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
  totalBalance?: number;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ pnl, pnlHours, onPnlHoursChange, totalBalance }) => {
  const { t } = useSettings();
  const points = pnl?.data_points ?? [];
  const total = pnl?.total_pnl ?? 0;
  const unrealized = pnl?.unrealized_pnl ?? 0;
  const realized = pnl?.realized_pnl ?? 0;

  const [hover, setHover] = useState<{ x: number; y: number; idx: number } | null>(null);

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
  const accentColor = '#3b82f6';
  const posColor = '#22a06b';
  const negColor = '#c65a58';
  const neutralColor = '#64748b';

  const closedFillPath = values.length > 1
    ? `${path} L ${scaleX(values.length - 1)} ${zeroY} L ${scaleX(0)} ${zeroY} Z`
    : '';

  // Stroke: gradient that switches color at zero line
  const zeroFrac = zeroY / height; // 0 = top, 1 = bottom
  const prevValueAtHover = hover && hover.idx > 0 ? values[hover.idx - 1] : null;
  const hoverDelta = hover && prevValueAtHover != null ? values[hover.idx] - prevValueAtHover : null;

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * width;
    // Find nearest data point
    if (values.length <= 1) return;
    const idx = Math.round((mouseX / width) * (values.length - 1));
    const clampedIdx = Math.max(0, Math.min(values.length - 1, idx));
    setHover({
      x: scaleX(clampedIdx),
      y: scaleY(values[clampedIdx]),
      idx: clampedIdx,
    });
  };

  const handleMouseLeave = () => setHover(null);

  // Current hovered chart point data
  const hoverPoint = hover ? chartPoints[hover.idx] : null;

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
            {totalBalance && totalBalance > 0 ? (
              <span className="nx-analytics-pct" style={{
                fontSize: '0.72em',
                marginLeft: 6,
                opacity: 0.75,
                fontWeight: 500,
              }}>
                ({total >= 0 ? '+' : ''}{((total / totalBalance) * 100).toFixed(2)}%)
              </span>
            ) : null}
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
          <div className="nx-chart-container">
            <svg
              width="100%"
              height="160"
              viewBox={`0 0 ${width} ${height}`}
              preserveAspectRatio="none"
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
            >
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
                  <stop offset={`${zeroFrac * 100}%`} stopColor={posColor} />
                  <stop offset={`${zeroFrac * 100}%`} stopColor={negColor} />
                </linearGradient>
              </defs>
              {[0.2, 0.4, 0.6, 0.8].map((ratio) => {
                const y = height * ratio;
                return (
                  <line
                    key={`grid-${ratio}`}
                    x1="0"
                    y1={y}
                    x2={width}
                    y2={y}
                    className="nx-chart-gridline"
                  />
                );
              })}
              {/* Zero baseline */}
              <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="rgba(148,163,184,0.35)" strokeWidth="1" strokeDasharray="4 4" />
              {/* Green fill — above zero */}
              <path d={closedFillPath} fill="rgba(34,160,107,0.09)" clipPath="url(#clip-above)" />
              {/* Red fill — below zero */}
              <path d={closedFillPath} fill="rgba(198,90,88,0.09)" clipPath="url(#clip-below)" />
              {/* Line with split color */}
              <path d={path} fill="none" stroke="url(#line-grad)" strokeWidth="1.85" strokeLinecap="round" strokeLinejoin="round" />
              {/* Crosshair */}
              {hover && (
                <>
                  <line x1={hover.x} y1={0} x2={hover.x} y2={height} className="nx-chart-crosshair" />
                  <circle cx={hover.x} cy={hover.y} r="4" fill={values[hover.idx] >= 0 ? posColor : negColor} stroke="rgba(241,245,249,0.9)" strokeWidth="1.25" />
                </>
              )}
            </svg>
            {/* Tooltip */}
            {hover && hoverPoint && (
              <div
                className="nx-chart-tooltip"
                style={{
                  left: `${(hover.x / width) * 100}%`,
                  top: hover.y > height / 2 ? 8 : undefined,
                  bottom: hover.y <= height / 2 ? 8 : undefined,
                }}
              >
                <div className="nx-chart-tooltip__row">
                  <span className="nx-chart-tooltip__dot" style={{ background: hoverPoint.cumulative_pnl >= 0 ? posColor : negColor }} />
                  {formatCurrency(hoverPoint.cumulative_pnl)}
                </div>
                {hoverDelta != null && (
                  <div className="nx-chart-tooltip__delta" style={{ color: hoverDelta >= 0 ? posColor : negColor }}>
                    {hoverDelta >= 0 ? '+' : ''}{formatCurrency(hoverDelta)}
                  </div>
                )}
                <div style={{ fontSize: 9, opacity: 0.6, marginTop: 2 }}>
                  {formatTime(hoverPoint.timestamp)}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-muted text-xs" style={{ padding: '40px 0', textAlign: 'center', color: neutralColor }}>{t.waitingPnl}</div>
        )}
      </div>
    </div>
  );
};

export default AnalyticsPanel;
