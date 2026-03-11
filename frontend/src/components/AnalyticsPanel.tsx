import React, { useState, useMemo, useCallback } from 'react';
import { useSettings } from '../context/SettingsContext';
import { formatCurrency } from '../utils/format';
import { SkeletonAnalyticsPanel } from './Skeleton';

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

/* ── Format helpers (module-level) ─────────────────────────────── */
function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatShortDate(ts: number, mode: 'time' | 'datetime' | 'date'): string {
  const d = new Date(ts * 1000);
  switch (mode) {
    case 'time':
      return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
    case 'datetime':
      return `${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })}`;
    case 'date':
    default:
      return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
}

/* ── Smooth path via Catmull-Rom → cubic Bezier ──────────────── */
function smoothPath(points: { x: number; y: number }[], tension: number = 0.3): string {
  if (points.length < 2) return '';
  if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;

  let d = `M ${points[0].x} ${points[0].y}`;

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(i - 1, 0)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(i + 2, points.length - 1)];

    const cp1x = p1.x + (p2.x - p0.x) * tension;
    const cp1y = p1.y + (p2.y - p0.y) * tension;
    const cp2x = p2.x - (p3.x - p1.x) * tension;
    const cp2y = p2.y - (p3.y - p1.y) * tension;

    d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2.x} ${p2.y}`;
  }

  return d;
}

/* ── Main component ───────────────────────────────────────────── */
const AnalyticsPanelInner: React.FC<AnalyticsPanelProps> = ({ pnl, pnlHours, onPnlHoursChange, totalBalance }) => {
  const { t } = useSettings();

  const points = (pnl as NonNullable<typeof pnl>).data_points ?? [];
  const total = (pnl as NonNullable<typeof pnl>).total_pnl ?? 0;
  const unrealized = (pnl as NonNullable<typeof pnl>).unrealized_pnl ?? 0;
  const realized = (pnl as NonNullable<typeof pnl>).realized_pnl ?? 0;

  const [hover, setHover] = useState<{ x: number; y: number; idx: number } | null>(null);

  const WIDTH = 700;
  const HEIGHT = 220;
  const PADDING_TOP = 35;
  const PADDING_BOTTOM = 30;
  const PADDING_LEFT = 50;
  const PADDING_RIGHT = 58;
  const chartW = WIDTH - PADDING_LEFT - PADDING_RIGHT;
  const chartH = HEIGHT - PADDING_TOP - PADDING_BOTTOM;

  const posColor = '#2dd4a0';
  const negColor = '#ef4444';
  const neutralColor = '#64748b';

  // Prepend zero-baseline point
  const chartPoints: PnlPoint[] = useMemo(() =>
    points.length > 0
      ? [{ pnl: 0, cumulative_pnl: 0, timestamp: points[0].timestamp - 60 }, ...points]
      : points,
    [points]
  );

  const values = useMemo(() => chartPoints.map((p) => p.cumulative_pnl), [chartPoints]);

  const { min, max, range } = useMemo(() => {
    if (values.length === 0) return { min: 0, max: 1, range: 1 };
    const mn = Math.min(...values);
    const mx = Math.max(...values);
    const padding = (mx - mn) * 0.12 || 0.5;
    return { min: mn - padding, max: mx + padding, range: (mx + padding) - (mn - padding) || 1 };
  }, [values]);

  const scaleX = useCallback((i: number) => PADDING_LEFT + (values.length <= 1 ? 0 : (i / (values.length - 1)) * chartW), [values.length, chartW]);
  const scaleY = useCallback((v: number) => PADDING_TOP + chartH - ((v - min) / range) * chartH, [min, range, chartH]);

  // Smooth curve points
  const curvePoints = useMemo(() =>
    values.map((v, i) => ({ x: scaleX(i), y: scaleY(v) })),
    [values, scaleX, scaleY]
  );

  const curvePath = useMemo(() => smoothPath(curvePoints, 0.25), [curvePoints]);

  // Zero baseline Y
  const zeroY = useMemo(() => Math.max(PADDING_TOP, Math.min(PADDING_TOP + chartH, scaleY(0))), [scaleY, chartH]);

  // Fill path (closed area under/above curve)
  const fillPathAbove = useMemo(() => {
    if (curvePoints.length < 2) return '';
    return `${curvePath} L ${curvePoints[curvePoints.length - 1].x} ${zeroY} L ${curvePoints[0].x} ${zeroY} Z`;
  }, [curvePath, curvePoints, zeroY]);

  // Y-axis gridlines
  const gridLines = useMemo(() => {
    const lines: { value: number; y: number }[] = [];
    const step = range / 5;
    for (let i = 0; i <= 5; i++) {
      const val = min + step * i;
      lines.push({ value: val, y: scaleY(val) });
    }
    return lines;
  }, [min, range, scaleY]);

  // X-axis labels — snapped to round intervals, format adapts to step size
  const dateLabels = useMemo(() => {
    if (chartPoints.length < 2) return [];
    const tsStart = chartPoints[0].timestamp;
    const tsEnd = chartPoints[chartPoints.length - 1].timestamp;
    const tsRange = tsEnd - tsStart;
    if (tsRange <= 0) return [];

    // Pick a round step size based on total range
    const targetCount = 6;
    const rawStep = tsRange / targetCount;
    // Round step candidates (seconds): 1h, 2h, 3h, 4h, 6h, 8h, 12h, 1d, 2d, 7d, 14d, 30d
    const steps = [3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400, 172800, 604800, 1209600, 2592000];
    const step = steps.find(s => s >= rawStep) ?? steps[steps.length - 1];

    // Format adapts to step: >=1d → date only, >=6h → date+time, <6h → time only
    const mode: 'time' | 'datetime' | 'date' =
      step >= 86400 ? 'date' : step >= 21600 ? 'datetime' : 'time';

    // First tick: round UP from tsStart to nearest step boundary
    const firstTick = Math.ceil(tsStart / step) * step;

    const labels: { label: string; x: number }[] = [];
    for (let ts = firstTick; ts <= tsEnd; ts += step) {
      const frac = (ts - tsStart) / tsRange;
      const x = PADDING_LEFT + frac * chartW;
      labels.push({ label: formatShortDate(ts, mode), x });
    }
    return labels;
  }, [chartPoints, chartW]);

  // Zero fraction for gradient split
  const zeroFrac = (zeroY - PADDING_TOP) / chartH;

  // Zero crossings: x positions where cumulative PnL transitions + ↔ −
  const zeroCrossings = useMemo(() => {
    if (values.length < 2) return [];
    const cxs: number[] = [];
    for (let i = 1; i < values.length; i++) {
      const pv = values[i - 1];
      const cv = values[i];
      if ((pv < 0 && cv >= 0) || (pv >= 0 && cv < 0)) {
        const frac = Math.abs(pv) / (Math.abs(pv) + Math.abs(cv));
        cxs.push(scaleX(i - 1) + frac * (scaleX(i) - scaleX(i - 1)));
      }
    }
    return cxs;
  }, [values, scaleX]);

  // Current value (last point)
  const currentValue = values.length > 0 ? values[values.length - 1] : 0;

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * WIDTH;
    if (values.length <= 1) return;
    const rawIdx = (mouseX - PADDING_LEFT) / chartW * (values.length - 1);
    const idx = Math.max(0, Math.min(values.length - 1, Math.round(rawIdx)));
    setHover({ x: scaleX(idx), y: scaleY(values[idx]), idx });
  }, [values, chartW, scaleX, scaleY]);

  const handleMouseLeave = useCallback(() => setHover(null), []);

  const hoverPoint = hover ? chartPoints[hover.idx] : null;
  const prevValue = hover && hover.idx > 0 ? values[hover.idx - 1] : null;
  const hoverDelta = hover && prevValue != null ? values[hover.idx] - prevValue : null;

  return (
    <div className="wh-chart-card">
      {/* ── Header: WARHUNTER-style ────────────────────────────── */}
      <div className="wh-chart-header">
        <div className="wh-chart-header__left">
          <div className="wh-chart-header__icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
              <polyline points="16 7 22 7 22 13" />
            </svg>
          </div>
          <div>
            <div className="wh-chart-header__title">RATEBRIDGE</div>
            <div className="wh-chart-header__subtitle">All-time P&L</div>
          </div>
        </div>
        <div className="wh-chart-header__right">
          <div className={`wh-chart-total ${total >= 0 ? 'wh-chart-total--pos' : 'wh-chart-total--neg'}`}>
            {formatCurrency(total)}
          </div>
          {totalBalance && totalBalance > 0 ? (
            <span className={`wh-chart-pct ${total >= 0 ? 'wh-chart-pct--pos' : 'wh-chart-pct--neg'}`}>
              {total >= 0 ? '+' : ''}{((total / totalBalance) * 100).toFixed(2)}%
            </span>
          ) : null}
        </div>
      </div>

      {/* ── Breakdown: Realized / Unrealized ───────────────────── */}
      {(unrealized !== 0 || realized !== 0) && (
        <div className="wh-chart-breakdown">
          <span className="wh-chart-breakdown__item">
            <span className="wh-chart-breakdown__dot" style={{ background: posColor }} />
            Realized {formatCurrency(realized)}
          </span>
          <span className="wh-chart-breakdown__item">
            <span className="wh-chart-breakdown__dot" style={{ background: unrealized >= 0 ? '#60a5fa' : negColor }} />
            Unrealized {formatCurrency(unrealized)}
          </span>
        </div>
      )}

      {/* ── Time pills ─────────────────────────────────────────── */}
      <div className="wh-chart-pills">
        {[
          { label: '24h', value: 24 },
          { label: '7d', value: 168 },
          { label: '30d', value: 720 },
          { label: '90d', value: 2160 },
          { label: 'All', value: 4320 },
        ].map((btn) => (
          <button
            key={btn.value}
            className={`wh-pill ${pnlHours === btn.value ? 'wh-pill--active' : ''}`}
            onClick={() => onPnlHoursChange(btn.value)}
          >
            {btn.label}
          </button>
        ))}
      </div>

      {/* ── Chart area ─────────────────────────────────────────── */}
      <div className="wh-chart-canvas">
        {values.length > 1 ? (
          <div className="wh-chart-svg-wrap">
            <svg
              width="100%"
              height="100%"
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              preserveAspectRatio="none"
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
              className="wh-chart-svg"
            >
              <defs>
                {/* Gradient fills */}
                <linearGradient id="wh-fill-pos" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={posColor} stopOpacity="0.25" />
                  <stop offset="100%" stopColor={posColor} stopOpacity="0.02" />
                </linearGradient>
                <linearGradient id="wh-fill-neg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={negColor} stopOpacity="0.02" />
                  <stop offset="100%" stopColor={negColor} stopOpacity="0.20" />
                </linearGradient>
                {/* Line gradient: green above zero, red below */}
                <linearGradient id="wh-line-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={`${Math.max(0, Math.min(100, zeroFrac * 100))}%`} stopColor={posColor} />
                  <stop offset={`${Math.max(0, Math.min(100, zeroFrac * 100))}%`} stopColor={negColor} />
                </linearGradient>
                {/* Glow filter */}
                <filter id="wh-glow">
                  <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                  <feMerge>
                    <feMergeNode in="coloredBlur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
                {/* Clip regions */}
                <clipPath id="wh-clip-above">
                  <rect x={PADDING_LEFT} y={PADDING_TOP} width={chartW} height={zeroY - PADDING_TOP} />
                </clipPath>
                <clipPath id="wh-clip-below">
                  <rect x={PADDING_LEFT} y={zeroY} width={chartW} height={PADDING_TOP + chartH - zeroY} />
                </clipPath>
              </defs>

              {/* Y-axis gridlines + labels */}
              {gridLines.map((gl, i) => (
                <g key={`grid-${i}`}>
                  <line
                    x1={PADDING_LEFT}
                    y1={gl.y}
                    x2={WIDTH - PADDING_RIGHT}
                    y2={gl.y}
                    stroke="rgba(148, 163, 184, 0.08)"
                    strokeWidth="1"
                  />
                  <text
                    x={PADDING_LEFT - 8}
                    y={gl.y + 3}
                    textAnchor="end"
                    className="wh-chart-axis-label"
                  >
                    {formatCurrency(gl.value)}
                  </text>
                </g>
              ))}

              {/* X-axis date labels */}
              {dateLabels.map((dl, i) => (
                <text
                  key={`date-${i}`}
                  x={dl.x}
                  y={HEIGHT - 6}
                  textAnchor="middle"
                  className="wh-chart-axis-label"
                >
                  {dl.label}
                </text>
              ))}

              {/* Zero baseline */}
              <line
                x1={PADDING_LEFT}
                y1={zeroY}
                x2={WIDTH - PADDING_RIGHT}
                y2={zeroY}
                stroke="rgba(148, 163, 184, 0.3)"
                strokeWidth="1"
                strokeDasharray="6 4"
              />

              {/* Zero-crossing glow markers — pulse ring where PnL flips sign */}
              {zeroCrossings.map((cx, ci) => (
                <g key={`zcross-${ci}`}>
                  <circle cx={cx} cy={zeroY} r="8" fill="none"
                    stroke="#2dd4a0" strokeWidth="1.5"
                    className="nx-zero-pulse" opacity="0.65" />
                  <circle cx={cx} cy={zeroY} r="2.5" fill="#2dd4a0" opacity="0.95" />
                </g>
              ))}

              {/* Green fill — above zero */}
              <path d={fillPathAbove} fill="url(#wh-fill-pos)" clipPath="url(#wh-clip-above)" />
              {/* Red fill — below zero */}
              <path d={fillPathAbove} fill="url(#wh-fill-neg)" clipPath="url(#wh-clip-below)" />

              {/* Main line with glow — re-keyed on data change to retrigger draw animation */}
              <path
                key={`draw-glow-${pnlHours}-${points.length}`}
                d={curvePath}
                className="wh-path-animated"
                pathLength="1"
                fill="none"
                stroke="url(#wh-line-grad)"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                filter="url(#wh-glow)"
              />
              {/* Sharp line on top */}
              <path
                key={`draw-sharp-${pnlHours}-${points.length}`}
                d={curvePath}
                className="wh-path-animated"
                pathLength="1"
                fill="none"
                stroke="url(#wh-line-grad)"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />

              {/* ── Current value dashed line + badge ─────────────── */}
              {values.length > 1 && (
                <>
                  <line
                    x1={scaleX(values.length - 1)}
                    y1={scaleY(currentValue)}
                    x2={WIDTH - PADDING_RIGHT}
                    y2={scaleY(currentValue)}
                    stroke={currentValue >= 0 ? posColor : negColor}
                    strokeWidth="1"
                    strokeDasharray="4 3"
                    opacity="0.5"
                  />
                  {/* Current value badge */}
                  <rect
                    x={WIDTH - PADDING_RIGHT + 2}
                    y={scaleY(currentValue) - 10}
                    width={56}
                    height={20}
                    rx={4}
                    fill={currentValue >= 0 ? posColor : negColor}
                    opacity="0.9"
                  />
                  <text
                    x={WIDTH - PADDING_RIGHT + 30}
                    y={scaleY(currentValue) + 4}
                    textAnchor="middle"
                    className="wh-badge-label"
                  >
                    {formatCurrency(currentValue)}
                  </text>
                </>
              )}

              {/* ── Crosshair + hover dot ─────────────────────────── */}
              {hover && (
                <>
                  <line
                    x1={hover.x}
                    y1={PADDING_TOP}
                    x2={hover.x}
                    y2={PADDING_TOP + chartH}
                    stroke="rgba(148, 163, 184, 0.3)"
                    strokeWidth="1"
                    strokeDasharray="3 3"
                  />
                  <line
                    x1={PADDING_LEFT}
                    y1={hover.y}
                    x2={WIDTH - PADDING_RIGHT}
                    y2={hover.y}
                    stroke="rgba(148, 163, 184, 0.15)"
                    strokeWidth="1"
                    strokeDasharray="3 3"
                  />
                  {/* Outer glow ring */}
                  <circle
                    cx={hover.x}
                    cy={hover.y}
                    r="8"
                    fill={values[hover.idx] >= 0 ? posColor : negColor}
                    opacity="0.15"
                  />
                  {/* Main dot */}
                  <circle
                    cx={hover.x}
                    cy={hover.y}
                    r="4.5"
                    fill={values[hover.idx] >= 0 ? posColor : negColor}
                    stroke="rgba(15, 23, 42, 0.9)"
                    strokeWidth="2"
                  />
                </>
              )}
            </svg>

            {/* ── Tooltip ─────────────────────────────────────────── */}
            {hover && hoverPoint && (
              <div
                className="wh-tooltip"
                style={{
                  left: `${((hover.x) / WIDTH) * 100}%`,
                  top: hover.y > (PADDING_TOP + chartH / 2) ? 12 : undefined,
                  bottom: hover.y <= (PADDING_TOP + chartH / 2) ? 12 : undefined,
                }}
              >
                <div className="wh-tooltip__value" style={{ color: hoverPoint.cumulative_pnl >= 0 ? posColor : negColor }}>
                  {formatCurrency(hoverPoint.cumulative_pnl)}
                </div>
                {hoverDelta != null && (
                  <div className="wh-tooltip__delta" style={{ color: hoverDelta >= 0 ? posColor : negColor }}>
                    {hoverDelta >= 0 ? '▲' : '▼'} {formatCurrency(Math.abs(hoverDelta))}
                  </div>
                )}
                <div className="wh-tooltip__time">
                  {formatTime(hoverPoint.timestamp)}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="wh-chart-empty">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke={neutralColor} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.4">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
              <polyline points="16 7 22 7 22 13" />
            </svg>
            <span>{t.waitingPnl}</span>
          </div>
        )}
      </div>
    </div>
  );
};

// Wrapper: guards null before mounting the inner component (avoids putting hooks after a conditional return).
const AnalyticsPanel: React.FC<AnalyticsPanelProps> = (props) => {
  if (props.pnl === null) return <SkeletonAnalyticsPanel />;
  return <AnalyticsPanelInner {...props} />;
};

export default AnalyticsPanel;
