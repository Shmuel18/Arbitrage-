import React, { memo, useState, useEffect, useRef } from 'react';
import { animate, AnimatePresence, m } from 'framer-motion';
import { StaggerReveal, StaggerItem } from './ViewReveal';
import { useSettings } from '../context/SettingsContext';
import { SkeletonStatsCards } from './Skeleton';

interface StatsCardsProps {
  totalBalance: number;
  dailyPnl: number;
  activeTrades: number;
  systemRunning: boolean;
  winRate?: number;
  totalTrades?: number;
  allTimePnl?: number;
  avgPnl?: number;
  isLoading?: boolean;
}

// Module-level formatter ג€” created once, not per render (avoids Intl overhead).
const _usdFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
});
const fmt = (v: number) => _usdFmt.format(v);

// ג”€ג”€ SVG icons ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€
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

// ג”€ג”€ Stable icon element instances ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€
// Passing `icon={<IconWallet />}` as a prop creates a new React element on
// every parent render, which defeats React.memo's shallow prop comparison on
// StatCard. These module-level constants ensure stable references.
const ICON_WALLET    = <IconWallet />;
const ICON_TREND_UP  = <IconTrendUp />;
const ICON_ACTIVITY  = <IconActivity />;
const ICON_SHIELD    = <IconShield />;
const ICON_BAR_CHART = <IconBarChart />;
const ICON_TARGET    = <IconTarget />;
const ICON_ZAP       = <IconZap />;
const ICON_LAYERS    = <IconLayers />;

// ג”€ג”€ Number animation helpers ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€
/** Extract the numeric core from a formatted stat string. Returns null for text. */
function parseStatNum(value: string): number | null {
  const stripped = value.replace(/[$,% +]/g, '').trim();
  const n = parseFloat(stripped);
  return isNaN(n) ? null : n;
}

/** Re-format an animated intermediate value to match the original string's format. */
function reformatFromNum(template: string, n: number): string {
  if (template.includes('$')) return _usdFmt.format(n);
  if (template.includes('%')) return `${n.toFixed(1)}%`;
  if (template.includes('.')) return n.toFixed(2);
  return String(Math.round(n));
}

// ג”€ג”€ Animated value: counts from 0 on mount, springs on updates ג”€ג”€ג”€
const AnimatedValue: React.FC<{ value: string }> = memo(({ value }) => {
  const targetNum = parseStatNum(value);
  const isNumeric = targetNum !== null;
  const [display, setDisplay] = useState<string>(() =>
    isNumeric ? reformatFromNum(value, 0) : value
  );
  const prevValueRef = useRef(value);
  const currentNumRef = useRef<number>(0);

  // Mount: count up from 0 so numbers visibly roll in.
  useEffect(() => {
    if (!isNumeric || targetNum === 0) { setDisplay(value); return; }
    const ctrl = animate(0, targetNum, {
      duration: 0.9,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => { currentNumRef.current = v; setDisplay(reformatFromNum(value, v)); },
      onComplete: () => { currentNumRef.current = targetNum; setDisplay(value); },
    });
    return () => ctrl.stop();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Subsequent updates: spring to new value.
  useEffect(() => {
    if (prevValueRef.current === value) return;
    prevValueRef.current = value;
    if (!isNumeric || targetNum === null) { setDisplay(value); return; }
    const from = currentNumRef.current;
    const ctrl = animate(from, targetNum, {
      type: 'spring', damping: 28, stiffness: 110,
      onUpdate: (v) => { currentNumRef.current = v; setDisplay(reformatFromNum(value, v)); },
      onComplete: () => { currentNumRef.current = targetNum; setDisplay(value); },
    });
    return () => ctrl.stop();
  }, [value, isNumeric, targetNum]);

  if (!isNumeric) {
    return (
      <AnimatePresence mode="popLayout" initial={false}>
        <m.span
          key={value}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
          style={{ display: 'block' }}
        >{value}</m.span>
      </AnimatePresence>
    );
  }
  return <span style={{ display: 'block' }}>{display}</span>;
});

// ג”€ג”€ Single stat card ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€
interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  subColor?: string;
  icon: React.ReactNode;
  accentVar: string;
  accentHex: string;
  trend?: 'up' | 'down' | 'neutral';
  live?: boolean;
  idx?: number;
}

const StatCard: React.FC<StatCardProps> = memo(({ label, value, sub, subColor, icon, accentHex, trend, live, idx = 0 }) => {
  const { t: cardT } = useSettings();
  // Flash the card border green/red when a numeric value changes direction.
  const [flashShadow, setFlashShadow] = useState<string | null>(null);
  const [tickClass, setTickClass] = useState('');
  const prevValueRef = useRef(value);

  useEffect(() => {
    if (prevValueRef.current === value) return;
    const prev = parseStatNum(prevValueRef.current);
    const curr = parseStatNum(value);
    prevValueRef.current = value;
    if (prev !== null && curr !== null && prev !== curr) {
      const dir = curr > prev ? 'up' : 'down';
      const glow = dir === 'up' ? '#10b981' : '#ef4444';
      setFlashShadow(`0 0 0 1.5px ${glow}99, 0 0 28px ${glow}44`);
      setTickClass(`nx-xcard-value--tick-${dir}`);
      const t1 = setTimeout(() => setFlashShadow(null), 550);
      const t2 = setTimeout(() => setTickClass(''), 380);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
  }, [value]);

  return (
    <div
      className="xcard nx-xcard"
      style={{
        '--xcard-accent': accentHex,
        animationDelay: `${idx * 60}ms`,
        boxShadow: flashShadow ?? undefined,
        transition: flashShadow ? 'box-shadow 0.1s ease' : 'box-shadow 0.7s ease',
      } as React.CSSProperties}
    >
      <div className="xcard-top">
        <div className="xcard-icon" style={{ color: accentHex }}>
          {icon}
        </div>
        {live && <span className="xcard-live"><span className="xcard-live-dot" />{cardT.live}</span>}
        {!live && trend && (
          <span className="nx-xcard-trend" style={{ color: trend === 'up' ? 'var(--green)' : trend === 'down' ? 'var(--red)' : 'var(--text-muted)' }}>
            {trend === 'up' ? <ArrowUp /> : trend === 'down' ? <ArrowDown /> : null}
          </span>
        )}
      </div>
      <div className="xcard-label">{label}</div>
      <div className="xcard-value" style={{ overflow: 'hidden', position: 'relative' }}>
        <span className={`nx-xcard-value${tickClass ? ` ${tickClass}` : ''}`} style={{ display: 'block' }}>
          <AnimatedValue value={value} />
        </span>
      </div>
      {sub && (
        <div className="xcard-sub" style={{ color: subColor ?? 'var(--text-muted)' }}>
          {sub}
        </div>
      )}
    </div>
  );
});

// ג”€ג”€ Main component ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€
const StatsCards: React.FC<StatsCardsProps> = ({
  totalBalance, dailyPnl, activeTrades, systemRunning,
  winRate = 0, totalTrades = 0, allTimePnl = 0, avgPnl = 0,
  isLoading = false,
}) => {
  const { t } = useSettings();

  if (isLoading) return <SkeletonStatsCards />;

  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;

  const balancePct = (pnl: number): string => {
    if (!totalBalance || totalBalance <= 0) return '';
    const pct = (pnl / totalBalance) * 100;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
  };

  return (
    <div className="nx-stats-layout">
      {/* ג”€ג”€ Primary hero row ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          textTransform: 'uppercase', color: 'var(--text-muted)', opacity: 0.6,
        }}>{t.sessionLabel}</span>
        <span style={{
          fontSize: 9, color: 'var(--text-muted)', opacity: 0.40,
          fontVariantNumeric: 'tabular-nums',
        }}>
          {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </span>
        <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, var(--card-border), transparent)' }} />
      </div>
      <StaggerReveal className="xcards-grid">
        <StaggerItem><StatCard label={t.totalBalance} value={fmt(totalBalance)} sub={t.subTotalAcross} icon={ICON_WALLET} accentVar="--accent" accentHex="#3b82f6" trend="neutral" live /></StaggerItem>
        <StaggerItem><StatCard label={t.dailyPnl} value={fmt(dailyPnl)} sub={(() => { const pct = balancePct(dailyPnl); const label = dailyPnl >= 0 ? t.subProfitableSession : t.subLossSession; return pct ? `${pct}  ·  ${label}` : label; })()} subColor={dailyPnl >= 0 ? 'var(--green)' : 'var(--red)'} icon={ICON_TREND_UP} accentVar="--green" accentHex={dailyPnl >= 0 ? '#10b981' : '#ef4444'} trend={dailyPnl >= 0 ? 'up' : 'down'} /></StaggerItem>
        <StaggerItem><StatCard label={t.activeTrades} value={String(activeTrades)} sub={activeTrades > 0 ? `${activeTrades} ${t.subPositionsOpen}` : t.subNoPositions} icon={ICON_ACTIVITY} accentVar="--teal" accentHex="#06b6d4" trend={activeTrades > 0 ? 'up' : 'neutral'} live={activeTrades > 0} /></StaggerItem>
        <StaggerItem><StatCard label={t.systemStatus} value={systemRunning ? t.running : t.stopped} sub={systemRunning ? t.subScanningMarkets : t.subBotIdle} subColor={systemRunning ? 'var(--green)' : 'var(--text-muted)'} icon={ICON_SHIELD} accentVar="--purple" accentHex={systemRunning ? '#8b5cf6' : '#6b7280'} trend={systemRunning ? 'up' : 'neutral'} live={systemRunning} /></StaggerItem>
      </StaggerReveal>

      {/* ג”€ג”€ Secondary stats row ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ג”€ */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, marginTop: 4 }}>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          textTransform: 'uppercase', color: 'var(--text-muted)', opacity: 0.6,
        }}>{t.allTimeLabel}</span>
        {totalTrades > 0 && (
          <span style={{
            fontSize: 9, color: 'var(--text-muted)', opacity: 0.40,
            fontVariantNumeric: 'tabular-nums',
          }}>
            {totalTrades.toLocaleString()} {t.tradesWord}
          </span>
        )}
        <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, var(--card-border), transparent)' }} />
      </div>
      <StaggerReveal className="xcards-grid">
        <StaggerItem><StatCard label={t.allTimePnl} value={fmt(allTimePnl)} sub={(() => { const pct = balancePct(allTimePnl); return pct ? `${pct}  .  ${t.subCumulativePnl}` : t.subCumulativePnl; })()} subColor={allTimePnl >= 0 ? 'var(--green)' : 'var(--red)'} icon={ICON_BAR_CHART} accentVar={allTimePnl >= 0 ? '--gold' : '--red'} accentHex={allTimePnl >= 0 ? '#d4a843' : '#ef4444'} trend={allTimePnl >= 0 ? 'up' : 'down'} idx={4} /></StaggerItem>
        <StaggerItem><StatCard label={t.winRate} value={fmtPct(winRate)} sub={`${Math.round(winRate * totalTrades)} / ${totalTrades} ${t.tradesWord}`} subColor={winRate >= 0.6 ? 'var(--green)' : winRate >= 0.4 ? 'var(--yellow)' : 'var(--red)'} icon={ICON_TARGET} accentVar={winRate >= 0.6 ? '--gold' : '--green'} accentHex={winRate >= 0.6 ? '#d4a843' : winRate >= 0.4 ? '#f59e0b' : '#ef4444'} trend={winRate >= 0.5 ? 'up' : 'down'} idx={5} /></StaggerItem>
        <StaggerItem><StatCard label={t.avgPnlStat} value={fmt(avgPnl)} sub={t.subPerClosedTrade} subColor={avgPnl >= 0 ? 'var(--green)' : 'var(--red)'} icon={ICON_ZAP} accentVar="--teal" accentHex="#06b6d4" trend={avgPnl >= 0 ? 'up' : 'down'} idx={6} /></StaggerItem>
        <StaggerItem><StatCard label={t.totalTradesLabel} value={String(totalTrades)} sub={t.subAllTimeExec} icon={ICON_LAYERS} accentVar="--purple" accentHex="#8b5cf6" trend={totalTrades > 0 ? 'up' : 'neutral'} idx={7} /></StaggerItem>
      </StaggerReveal>
    </div>
  );
};

export default StatsCards;
