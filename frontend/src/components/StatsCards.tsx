import React from 'react';
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

// ── Mini sparkline ───────────────────────────────────────────────
const MiniSparkline: React.FC<{ positive: boolean; accent: string }> = ({ positive, accent }) => {
  const path = positive
    ? "M0,18 C5,16 10,12 15,10 C20,8 25,5 30,3"
    : "M0,3 C5,5 10,8 15,11 C20,14 25,16 30,18";
  return (
    <svg width="30" height="21" viewBox="0 0 30 21" fill="none" style={{ opacity: 0.5 }}>
      <path d={path} stroke={accent} strokeWidth="1.5" strokeLinecap="round" fill="none"/>
    </svg>
  );
};

// ── Single stat card ─────────────────────────────────────────────
interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  subColor?: string;
  icon: React.ReactNode;
  accentVar: string;        // CSS var name e.g. '--accent'
  accentHex: string;        // for direct use
  trend?: 'up' | 'down' | 'neutral';
  live?: boolean;
}

const StatCard: React.FC<StatCardProps> = ({ label, value, sub, subColor, icon, accentVar, accentHex, trend, live }) => (
  <div className="xcard" style={{ '--xcard-accent': accentHex } as React.CSSProperties}>
    <div className="xcard-top">
      <div className="xcard-icon" style={{ color: accentHex }}>
        {icon}
      </div>
      {live && <span className="xcard-live"><span className="xcard-live-dot" />LIVE</span>}
      {!live && trend && (
        <span className="xcard-trend" style={{ color: trend === 'up' ? 'var(--green)' : trend === 'down' ? 'var(--red)' : 'var(--text-muted)' }}>
          {trend === 'up' ? <ArrowUp /> : trend === 'down' ? <ArrowDown /> : null}
        </span>
      )}
    </div>
    <div className="xcard-label">{label}</div>
    <div className="xcard-value">{value}</div>
    {sub && (
      <div className="xcard-sub" style={{ color: subColor ?? 'var(--text-muted)' }}>
        {sub}
      </div>
    )}
    {trend && trend !== 'neutral' && (
      <div className="xcard-sparkline">
        <MiniSparkline positive={trend === 'up'} accent={accentHex} />
      </div>
    )}
  </div>
);

// ── Main component ───────────────────────────────────────────────
const StatsCards: React.FC<StatsCardsProps> = ({
  totalBalance, dailyPnl, activeTrades, systemRunning,
  winRate = 0, totalTrades = 0, allTimePnl = 0, avgPnl = 0,
}) => {
  const { t } = useSettings();

  const fmt = (v: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(v);
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="xcards-grid">
      <StatCard
        label={t.totalBalance}
        value={fmt(totalBalance)}
        sub="Total across all exchanges"
        icon={<IconWallet />}
        accentVar="--accent"
        accentHex="#3b82f6"
        trend="neutral"
        live
      />
      <StatCard
        label={t.dailyPnl}
        value={fmt(dailyPnl)}
        sub={dailyPnl >= 0 ? '▲ Profitable session' : '▼ Loss session'}
        subColor={dailyPnl >= 0 ? 'var(--green)' : 'var(--red)'}
        icon={<IconTrendUp />}
        accentVar="--green"
        accentHex={dailyPnl >= 0 ? '#10b981' : '#ef4444'}
        trend={dailyPnl >= 0 ? 'up' : 'down'}
      />
      <StatCard
        label={t.activeTrades}
        value={String(activeTrades)}
        sub={activeTrades > 0 ? `${activeTrades} position${activeTrades !== 1 ? 's' : ''} open` : 'No open positions'}
        icon={<IconActivity />}
        accentVar="--teal"
        accentHex="#06b6d4"
        trend={activeTrades > 0 ? 'up' : 'neutral'}
        live={activeTrades > 0}
      />
      <StatCard
        label={t.systemStatus}
        value={systemRunning ? t.running : t.stopped}
        sub={systemRunning ? 'Scanning markets' : 'Bot is idle'}
        subColor={systemRunning ? 'var(--green)' : 'var(--text-muted)'}
        icon={<IconShield />}
        accentVar="--purple"
        accentHex={systemRunning ? '#8b5cf6' : '#6b7280'}
        trend={systemRunning ? 'up' : 'neutral'}
        live={systemRunning}
      />
      <StatCard
        label={t.allTimePnl}
        value={fmt(allTimePnl)}
        sub="Cumulative P&L"
        subColor={allTimePnl >= 0 ? 'var(--green)' : 'var(--red)'}
        icon={<IconBarChart />}
        accentVar="--accent"
        accentHex="#3b82f6"
        trend={allTimePnl >= 0 ? 'up' : 'down'}
      />
      <StatCard
        label={t.winRate}
        value={fmtPct(winRate)}
        sub={`${Math.round(winRate * totalTrades)} / ${totalTrades} trades`}
        subColor={winRate >= 0.6 ? 'var(--green)' : winRate >= 0.4 ? 'var(--yellow)' : 'var(--red)'}
        icon={<IconTarget />}
        accentVar="--green"
        accentHex={winRate >= 0.6 ? '#10b981' : '#f59e0b'}
        trend={winRate >= 0.5 ? 'up' : 'down'}
      />
      <StatCard
        label={t.avgPnlStat}
        value={fmt(avgPnl)}
        sub="Per closed trade"
        subColor={avgPnl >= 0 ? 'var(--green)' : 'var(--red)'}
        icon={<IconZap />}
        accentVar="--teal"
        accentHex="#06b6d4"
        trend={avgPnl >= 0 ? 'up' : 'down'}
      />
      <StatCard
        label={t.totalTradesLabel}
        value={String(totalTrades)}
        sub="All-time executions"
        icon={<IconLayers />}
        accentVar="--purple"
        accentHex="#8b5cf6"
        trend={totalTrades > 0 ? 'up' : 'neutral'}
      />
    </div>
  );
};

export default StatsCards;
