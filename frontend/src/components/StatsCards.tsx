import React, { memo, useMemo, useState } from 'react';
import { useSettings } from '../context/SettingsContext';

interface StatsCardsProps {
  totalBalance: number;
  dailyPnl: number;
  activeTrades: number;
  systemRunning: boolean;
  winRate?: number;
  totalTrades?: number;
  allTimePnl?: number;
  avgPnl?: number;
}

// ── SVG icons ────────────────────────────────────────────────────
const IconWallet = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 12V8a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-4"/>
    <path d="M14 12a2 2 0 0 0 2 2h4v-4h-4a2 2 0 0 0-2 2z"/>
  </svg>
);
const IconTrendUp = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/>
    <polyline points="16 7 22 7 22 13"/>
  </svg>
);
const IconActivity = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
  </svg>
);
const IconShield = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  </svg>
);
const IconBarChart = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10"/>
    <line x1="12" y1="20" x2="12" y2="4"/>
    <line x1="6" y1="20" x2="6" y2="14"/>
  </svg>
);
const IconTarget = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>
  </svg>
);
const IconZap = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
  </svg>
);
const IconLayers = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 2 7 12 12 22 7 12 2"/>
    <polyline points="2 17 12 22 22 17"/>
    <polyline points="2 12 12 17 22 12"/>
  </svg>
);

const ArrowUp = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>
  </svg>
);
const ArrowDown = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>
  </svg>
);

// ── Mini sparkline removed: hardcoded SVG paths were decorative, not real data.
// Replace with real per-card timeseries when available.

// ── Single stat card ─────────────────────────────────────────────
/**
 * Semantic intent for a stat card.
 * Drives the accent color via CSS variables, so theme changes
 * cascade without touching the component.
 */
type StatIntent = 'info' | 'profit' | 'loss' | 'neutral' | 'system' | 'warning';

// Unified accent — all stat cards share the brand teal so the grid
// reads as one cohesive surface. Semantic meaning (profit / loss /
// warning) is still carried via the sub-line text color.
const INTENT_VAR: Record<StatIntent, string> = {
  info:    'var(--brand-teal)',
  profit:  'var(--brand-teal)',
  loss:    'var(--brand-teal)',
  neutral: 'var(--brand-teal)',
  system:  'var(--brand-teal)',
  warning: 'var(--brand-teal)',
};

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  subColor?: string;
  icon: React.ReactNode;
  intent: StatIntent;
  trend?: 'up' | 'down' | 'neutral';
  live?: boolean;
  idx?: number;
}

const StatCard: React.FC<StatCardProps> = memo(({ label, value, sub, subColor, icon, intent, trend, live, idx = 0 }) => {
  const accent = INTENT_VAR[intent];
  return (
    <div
      className={`xcard nx-xcard nx-xcard--${intent}`}
      style={{ '--xcard-accent': accent, animationDelay: `${idx * 60}ms` } as React.CSSProperties}
      data-intent={intent}
    >
      <div className="xcard-top">
        <div className="xcard-icon" style={{ color: accent }}>
          {icon}
        </div>
        {live && <span className="xcard-live"><span className="xcard-live-dot" />LIVE</span>}
        {!live && trend && (
          <span className="nx-xcard-trend" style={{ color: trend === 'up' ? 'var(--color-profit)' : trend === 'down' ? 'var(--color-loss)' : 'var(--text-muted)' }}>
            {trend === 'up' ? <ArrowUp /> : trend === 'down' ? <ArrowDown /> : null}
          </span>
        )}
      </div>
      <div className="xcard-label">{label}</div>
      <div className="xcard-value nx-xcard-value">{value}</div>
      {sub && (
        <div className="xcard-sub" style={{ color: subColor ?? 'var(--text-muted)' }}>
          {sub}
        </div>
      )}
    </div>
  );
});

// ── Main component ───────────────────────────────────────────────
const StatsCards: React.FC<StatsCardsProps> = ({
  totalBalance, dailyPnl, activeTrades, systemRunning,
  winRate = 0, totalTrades = 0, allTimePnl = 0, avgPnl = 0,
}) => {
  const { t } = useSettings();
  const [allTimeOpen, setAllTimeOpen] = useState<boolean>(() => {
    const stored = localStorage.getItem('rb_alltime_open');
    return stored === null ? true : stored === '1';
  });
  const toggleAllTime = () => {
    setAllTimeOpen((o) => {
      const next = !o;
      localStorage.setItem('rb_alltime_open', next ? '1' : '0');
      return next;
    });
  };

  const fmt = useMemo(() => {
    const nf = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
    return (v: number) => nf.format(v);
  }, []);
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;

  const balancePct = (pnl: number): string => {
    if (!totalBalance || totalBalance <= 0) return '';
    const pct = (pnl / totalBalance) * 100;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
  };

  return (
    <div className="nx-stats-layout">
      {/* ── Primary hero row ───────────────────── */}
      <div className="xcards-grid xcards-grid--primary">
        <StatCard
          label={t.totalBalance}
          value={fmt(totalBalance)}
          sub={t.subTotalAcross}
          icon={<IconWallet />}
          intent="info"
          trend="neutral"
          live
        />
        <StatCard
          label={t.dailyPnl}
          value={fmt(dailyPnl)}
          sub={(() => {
            const pct = balancePct(dailyPnl);
            const label = dailyPnl >= 0 ? t.subProfitableSession : t.subLossSession;
            return pct ? `${pct}  ·  ${label}` : label;
          })()}
          subColor={dailyPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)'}
          icon={<IconTrendUp />}
          intent={dailyPnl >= 0 ? 'profit' : 'loss'}
          trend={dailyPnl >= 0 ? 'up' : 'down'}
        />
        <StatCard
          label={t.activeTrades}
          value={String(activeTrades)}
          sub={activeTrades > 0 ? `${activeTrades} ${t.subPositionsOpen}` : t.subNoPositions}
          icon={<IconActivity />}
          intent="neutral"
          trend={activeTrades > 0 ? 'up' : 'neutral'}
          live={activeTrades > 0}
        />
        <StatCard
          label={t.systemStatus}
          value={systemRunning ? t.running : t.stopped}
          sub={systemRunning ? t.subScanningMarkets : t.subBotIdle}
          subColor={systemRunning ? 'var(--color-profit)' : 'var(--text-muted)'}
          icon={<IconShield />}
          intent={systemRunning ? 'system' : 'neutral'}
          trend={systemRunning ? 'up' : 'neutral'}
          live={systemRunning}
        />
      </div>

      {/* ── Secondary stats: collapsible "All-Time" group ────────── */}
      <div className="nx-section-toggle-wrap">
        <button
          type="button"
          className={`nx-section-toggle${allTimeOpen ? ' nx-section-toggle--open' : ''}`}
          onClick={toggleAllTime}
          aria-expanded={allTimeOpen}
          aria-controls="nx-alltime-stats"
        >
          <span className="nx-section-toggle-label">ALL-TIME STATS</span>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ transform: allTimeOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      <div
        id="nx-alltime-stats"
        className={`xcards-grid xcards-grid--secondary${allTimeOpen ? '' : ' xcards-grid--collapsed'}`}
        style={{ display: allTimeOpen ? undefined : 'none' }}
      >
        <StatCard
          label={t.allTimePnl}
          value={fmt(allTimePnl)}
          sub={(() => {
            const pct = balancePct(allTimePnl);
            return pct ? `${pct}  ·  ${t.subCumulativePnl}` : t.subCumulativePnl;
          })()}
          subColor={allTimePnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)'}
          icon={<IconBarChart />}
          intent={allTimePnl >= 0 ? 'profit' : 'loss'}
          trend={allTimePnl >= 0 ? 'up' : 'down'}
          idx={4}
        />
        <StatCard
          label={t.winRate}
          value={fmtPct(winRate)}
          sub={`${Math.round(winRate * totalTrades)} / ${totalTrades} trades`}
          subColor={winRate >= 0.6 ? 'var(--color-profit)' : winRate >= 0.4 ? 'var(--color-warning)' : 'var(--color-loss)'}
          icon={<IconTarget />}
          intent={winRate >= 0.6 ? 'profit' : 'warning'}
          trend={winRate >= 0.5 ? 'up' : 'down'}
          idx={5}
        />
        <StatCard
          label={t.avgPnlStat}
          value={fmt(avgPnl)}
          sub={t.subPerClosedTrade}
          subColor={avgPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)'}
          icon={<IconZap />}
          intent="neutral"
          trend={avgPnl >= 0 ? 'up' : 'down'}
          idx={6}
        />
        <StatCard
          label={t.totalTradesLabel}
          value={String(totalTrades)}
          sub={t.subAllTimeExec}
          icon={<IconLayers />}
          intent="system"
          trend={totalTrades > 0 ? 'up' : 'neutral'}
          idx={7}
        />
      </div>
    </div>
  );
};

export default StatsCards;
