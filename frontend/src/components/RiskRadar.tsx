import React, { memo, useMemo } from 'react';
import { useSettings } from '../context/SettingsContext';

interface PositionRow {
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  [k: string]: unknown;
}

interface RiskRadarProps {
  positions: PositionRow[];
  totalBalance: number;
  dailyPnl: number;
  allTimePnl: number;
}

/* ── Color helpers ────────────────────── */
function riskColor(pct: number): string {
  if (pct <= 30) return '#22c55e';
  if (pct <= 60) return '#f59e0b';
  return '#ef4444';
}

function riskLabel(pct: number, t: { rrLow: string; rrModerate: string; rrHigh: string }): string {
  if (pct <= 30) return t.rrLow;
  if (pct <= 60) return t.rrModerate;
  return t.rrHigh;
}

/* ── Compute concentration via Herfindahl index ── */
function herfindahl(counts: Record<string, number>, total: number): number {
  if (total === 0) return 0;
  let hhi = 0;
  for (const key of Object.keys(counts)) {
    const share = counts[key] / total;
    hhi += share * share;
  }
  // Normalize: 1/N (perfectly spread) → 0%, 1 (100% concentrated) → 100%
  const n = Object.keys(counts).length;
  if (n <= 1) return 100;
  const minHhi = 1 / n;
  return Math.round(((hhi - minHhi) / (1 - minHhi)) * 100);
}

const RiskRadar: React.FC<RiskRadarProps> = memo(({
  positions,
  totalBalance,
  dailyPnl,
  allTimePnl,
}) => {
  const { t } = useSettings();

  const metrics = useMemo(() => {
    // ── Max drawdown (session) ──
    // Approximation: worst daily PnL as % of balance
    const drawdownPct = totalBalance > 0
      ? Math.max(0, Math.round((-dailyPnl / totalBalance) * 100))
      : 0;

    // ── Margin utilization ──
    // Number of positions relative to a reasonable max (we use 8 as reference)
    const posCount = positions.length;
    const marginPct = Math.min(100, Math.round((posCount / 8) * 100));

    // ── Symbol concentration ──
    const symbolCounts: Record<string, number> = {};
    const exchangeCounts: Record<string, number> = {};
    for (const pos of positions) {
      const sym = pos.symbol || 'unknown';
      symbolCounts[sym] = (symbolCounts[sym] || 0) + 1;
      const longEx = (pos.long_exchange || '') as string;
      const shortEx = (pos.short_exchange || '') as string;
      if (longEx) exchangeCounts[longEx] = (exchangeCounts[longEx] || 0) + 1;
      if (shortEx) exchangeCounts[shortEx] = (exchangeCounts[shortEx] || 0) + 1;
    }
    const symbolConc = posCount > 0 ? herfindahl(symbolCounts, posCount) : 0;
    const exchangeConc = posCount > 0
      ? herfindahl(exchangeCounts, posCount * 2 /* 2 legs per trade */)
      : 0;

    return { drawdownPct, marginPct, symbolConc, exchangeConc };
  }, [positions, totalBalance, dailyPnl]);

  const cells = [
    {
      label: t.rrMaxDrawdown,
      value: `${metrics.drawdownPct}%`,
      pct: metrics.drawdownPct,
      accent: riskColor(metrics.drawdownPct),
      hint: riskLabel(metrics.drawdownPct, t),
    },
    {
      label: t.rrMarginUsed,
      value: `${positions.length} / 8`,
      pct: metrics.marginPct,
      accent: riskColor(metrics.marginPct),
      hint: `${metrics.marginPct}%`,
    },
    {
      label: t.rrSymbolConc,
      value: `${metrics.symbolConc}%`,
      pct: metrics.symbolConc,
      accent: riskColor(metrics.symbolConc),
      hint: riskLabel(metrics.symbolConc, t),
    },
    {
      label: t.rrExchangeConc,
      value: `${metrics.exchangeConc}%`,
      pct: metrics.exchangeConc,
      accent: riskColor(metrics.exchangeConc),
      hint: riskLabel(metrics.exchangeConc, t),
    },
  ];

  return (
    <div className="risk-radar">
      {cells.map((cell) => (
        <div
          key={cell.label}
          className="risk-radar__cell"
          style={{ '--rr-accent': cell.accent } as React.CSSProperties}
        >
          <div className="risk-radar__label">{cell.label}</div>
          <div className="risk-radar__value" style={{ color: cell.accent }}>
            {cell.value}
          </div>
          <div className="risk-radar__bar-track">
            <div
              className="risk-radar__bar-fill"
              style={{
                width: `${Math.min(100, cell.pct)}%`,
                background: cell.accent,
              }}
            />
          </div>
          <div className="risk-radar__hint">{cell.hint}</div>
        </div>
      ))}
    </div>
  );
});

RiskRadar.displayName = 'RiskRadar';

export default RiskRadar;
