/**
 * Skeleton — shimmer loading placeholders.
 *
 * Mirrors the exact DOM structure of the real components so layout
 * doesn't shift when data arrives.
 */
import React, { memo } from 'react';

// ── Base shimmer primitive ───────────────────────────────────────
interface SkeleProps {
  className?: string;
  style?: React.CSSProperties;
  height?: string | number;
  width?: string | number;
  borderRadius?: string | number;
}

const Skele: React.FC<SkeleProps> = ({ className = '', style, height, width, borderRadius = 6 }) => (
  <div
    className={`skeleton-shimmer ${className}`}
    style={{ height, width, borderRadius, flexShrink: 0, ...style }}
  />
);

// ── Stat card skeleton (mirrors xcard) ──────────────────────────
export const SkeletonStatCard: React.FC<{ idx?: number }> = memo(({ idx = 0 }) => (
  <div
    className="xcard"
    style={{ animationDelay: `${idx * 60}ms`, pointerEvents: 'none' }}
  >
    <div className="xcard-top">
      <Skele width={32} height={32} borderRadius={8} />
    </div>
    <Skele height={11} width="55%" borderRadius={4} style={{ marginTop: 8 }} />
    <Skele height={28} width="75%" borderRadius={6} style={{ marginTop: 6 }} />
    <Skele height={10} width="60%" borderRadius={4} style={{ marginTop: 6 }} />
  </div>
));

// ── Full StatsCards skeleton — 4 + 4 grid ───────────────────────
export const SkeletonStatsCards: React.FC = memo(() => (
  <div className="nx-stats-layout">
    <div className="xcards-grid xcards-grid--primary">
      {[0, 1, 2, 3].map((i) => <SkeletonStatCard key={i} idx={i} />)}
    </div>
    <div className="xcards-grid xcards-grid--secondary">
      {[4, 5, 6, 7].map((i) => <SkeletonStatCard key={i} idx={i} />)}
    </div>
  </div>
));

// ── Single position row skeleton ────────────────────────────────
const SkeletonPositionRow: React.FC<{ idx?: number }> = memo(({ idx = 0 }) => (
  <div
    className="active-trade-card"
    style={{ borderRadius: 14, overflow: 'hidden', animationDelay: `${idx * 80}ms`, pointerEvents: 'none' }}
  >
    {/* Top accent line */}
    <div style={{ height: 2, background: 'var(--card-border)' }} />

    <div style={{ padding: '14px 16px 12px' }}>
      {/* Row 1: symbol + pnl number */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Skele height={18} width={90} borderRadius={5} />
          <div style={{ display: 'flex', gap: 6 }}>
            <Skele height={16} width={64} borderRadius={4} />
            <Skele height={16} width={44} borderRadius={4} />
          </div>
        </div>
        <Skele height={38} width={72} borderRadius={6} />
      </div>

      {/* Row 2: 4-column stat grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0 4px', marginBottom: 10 }}>
        {[0, 1, 2, 3].map((c) => (
          <div key={c} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <Skele height={9} width="70%" borderRadius={3} />
            <Skele height={14} width="55%" borderRadius={3} />
          </div>
        ))}
      </div>

      {/* Row 3: info bar */}
      <div style={{ display: 'flex', gap: 10 }}>
        <Skele height={9} width={80} borderRadius={3} />
        <Skele height={9} width={72} borderRadius={3} />
        <Skele height={9} width={60} borderRadius={3} />
      </div>
    </div>
  </div>
));

// ── Positions table skeleton with card shell ─────────────────────
export const SkeletonPositionsTable: React.FC<{ rows?: number }> = memo(({ rows = 3 }) => (
  <div className="card" style={{ position: 'relative' }}>
    {/* Top accent gradient */}
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, height: 2,
      background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.3), transparent)',
      borderRadius: '14px 14px 0 0', zIndex: 1, pointerEvents: 'none',
    }} />

    {/* Header */}
    <div className="card-header px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <Skele height={22} width={22} borderRadius={6} />
      <Skele height={13} width={130} borderRadius={4} />
    </div>

    {/* Rows */}
    <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonPositionRow key={i} idx={i} />
      ))}
    </div>
  </div>
));

export default Skele;

// ── Card shell helper (shared by several skeletons) ───────────────
const SkeleCardShell: React.FC<{ accentColor: string; headerWidth?: number; children: React.ReactNode }> = ({ accentColor, headerWidth = 140, children }) => (
  <div className="card" style={{ position: 'relative' }}>
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, height: 2,
      background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`,
      borderRadius: '14px 14px 0 0', zIndex: 1, pointerEvents: 'none',
    }} />
    <div className="card-header px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <Skele height={22} width={22} borderRadius={6} />
      <Skele height={13} width={headerWidth} borderRadius={4} />
    </div>
    {children}
  </div>
);

// ── RecentTradesPanel skeleton — 7-column table ───────────────────
const SkeletonTradeRow: React.FC<{ idx?: number }> = memo(({ idx = 0 }) => (
  <tr style={{ animationDelay: `${idx * 40}ms` }}>
    {/* symbol+tier */}
    <td><Skele height={13} width="80%" borderRadius={4} /></td>
    {/* long→short */}
    <td><Skele height={11} width="75%" borderRadius={4} /></td>
    {/* net pnl */}
    <td style={{ textAlign: 'right' }}><Skele height={13} width={56} borderRadius={4} style={{ marginInlineStart: 'auto' }} /></td>
    {/* funding */}
    <td style={{ textAlign: 'right' }}><Skele height={13} width={52} borderRadius={4} style={{ marginInlineStart: 'auto' }} /></td>
    {/* exit reason */}
    <td style={{ textAlign: 'right' }}><Skele height={18} width={68} borderRadius={4} style={{ marginInlineStart: 'auto' }} /></td>
    {/* duration */}
    <td style={{ textAlign: 'right' }}><Skele height={11} width={36} borderRadius={4} style={{ marginInlineStart: 'auto' }} /></td>
    {/* closed */}
    <td style={{ textAlign: 'right' }}><Skele height={11} width={72} borderRadius={4} style={{ marginInlineStart: 'auto' }} /></td>
  </tr>
));

export const SkeletonRecentTrades: React.FC<{ rows?: number }> = memo(({ rows = 6 }) => (
  <SkeleCardShell accentColor="rgba(34,197,94,0.3)" headerWidth={120}>
    <div className="overflow-auto">
      <table className="corp-table" style={{ tableLayout: 'fixed', width: '100%' }}>
        <colgroup>
          <col style={{ width: '18%' }} />
          <col style={{ width: '15%' }} />
          <col style={{ width: '12%' }} />
          <col style={{ width: '12%' }} />
          <col style={{ width: '14%' }} />
          <col style={{ width: '8%' }} />
          <col style={{ width: '21%' }} />
        </colgroup>
        <thead>
          <tr>
            {[100, 80, 60, 60, 70, 40, 80].map((w, i) => (
              <th key={i}><Skele height={10} width={w} borderRadius={3} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, i) => <SkeletonTradeRow key={i} idx={i} />)}
        </tbody>
      </table>
    </div>
  </SkeleCardShell>
));

// ── ExchangeBalances skeleton ─────────────────────────────────────
export const SkeletonExchangeBalances: React.FC<{ rows?: number }> = memo(({ rows = 3 }) => (
  <div className="card p-5" style={{ position: 'relative' }}>
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, height: 2,
      background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.3), transparent)',
      borderRadius: '14px 14px 0 0',
    }} />
    {/* Header row: title + total */}
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Skele height={22} width={22} borderRadius={6} />
        <Skele height={13} width={130} borderRadius={4} />
      </div>
      <Skele height={16} width={80} borderRadius={4} />
    </div>
    {/* Exchange rows */}
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{ animationDelay: `${i * 60}ms` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <Skele height={12} width={80} borderRadius={4} />
            <div style={{ display: 'flex', gap: 8 }}>
              <Skele height={12} width={70} borderRadius={4} />
              <Skele height={12} width={36} borderRadius={4} />
            </div>
          </div>
          <Skele height={6} width="100%" borderRadius={3} />
        </div>
      ))}
    </div>
  </div>
));

// ── AnalyticsPanel skeleton ───────────────────────────────────────
export const SkeletonAnalyticsPanel: React.FC = memo(() => (
  <SkeleCardShell accentColor="rgba(59,130,246,0.3)" headerWidth={160}>
    <div style={{ padding: '16px 20px 20px' }}>
      {/* Period selector pills */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {[40, 40, 40, 40, 48].map((w, i) => (
          <Skele key={i} height={26} width={w} borderRadius={6} />
        ))}
      </div>
      {/* Summary row */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{ flex: 1 }}>
            <Skele height={10} width="60%" borderRadius={3} style={{ marginBottom: 6 }} />
            <Skele height={22} width="80%" borderRadius={5} />
          </div>
        ))}
      </div>
      {/* Chart area */}
      <Skele height={220} width="100%" borderRadius={8} />
    </div>
  </SkeleCardShell>
));

// ── Opportunities (RightPanel) skeleton ──────────────────────────
const SkeletonOppRow: React.FC<{ idx?: number }> = memo(({ idx = 0 }) => (
  <div style={{
    padding: '10px 14px',
    borderBottom: '1px solid var(--card-border)',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    gap: 12, animationDelay: `${idx * 45}ms`,
  }}>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, flex: 1 }}>
      <Skele height={13} width={80} borderRadius={4} />
      <div style={{ display: 'flex', gap: 6 }}>
        <Skele height={10} width={55} borderRadius={3} />
        <Skele height={10} width={50} borderRadius={3} />
      </div>
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 5 }}>
      <Skele height={14} width={56} borderRadius={4} />
      <Skele height={10} width={40} borderRadius={3} />
    </div>
  </div>
));

export const SkeletonRightPanel: React.FC<{ rows?: number }> = memo(({ rows = 8 }) => (
  <SkeleCardShell accentColor="rgba(139,92,246,0.3)" headerWidth={150}>
    <div style={{ padding: '4px 0 8px' }}>
      {Array.from({ length: rows }, (_, i) => <SkeletonOppRow key={i} idx={i} />)}
    </div>
  </SkeleCardShell>
));
