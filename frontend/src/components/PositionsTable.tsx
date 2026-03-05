import React, { useState } from 'react';
import { useSettings } from '../context/SettingsContext';
import PositionDetailCard from './PositionDetailCard';

interface PositionRow {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_qty: string;
  short_qty: string;
  entry_edge_pct: string;
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  immediate_spread_pct?: string | null;
  current_spread_pct?: string | null;
  current_long_rate?: string | null;
  current_short_rate?: string | null;
  entry_price_long?: string | null;
  entry_price_short?: string | null;
  live_price_long?: string | null;
  live_price_short?: string | null;
  next_funding_ms?: number | null;
  mode?: string;
  entry_tier?: string | null;
  unrealized_pnl_pct?: string | null;
  price_pnl_pct?: string | null;
  funding_pnl_pct?: string | null;
  fees_pct?: string | null;
  entry_basis_pct?: string | null;
  current_basis_pct?: string | null;
  price_spread_pct?: string | null;
  funding_collected_usd?: string | null;
  fees_paid_total?: string | null;
  funding_collections?: number | null;
  profit_target_pct?: string | null;
  pending_income_usd?: string | null;
  pending_income_pct?: string | null;
  pending_net_usd?: string | null;
  pending_net_pct?: string | null;
  state: string;
}

interface PositionsTableProps {
  positions: PositionRow[];
}

const PositionsTable: React.FC<PositionsTableProps> = ({ positions }) => {
  const { t } = useSettings();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggleExpand = (id: string) => {
    setExpandedId(prev => prev === id ? null : id);
  };

  // ── Helpers ──────────────────────────────────────────────────
  const num = (v?: string | null): number | null => {
    if (v == null || v === '') return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  };

  const fmtPct = (v?: string | null, decimals = 3): string => {
    const n = num(v);
    if (n == null) return '--';
    return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`;
  };

  const fmtFunding = (rate?: string | null) => {
    if (!rate) return '--';
    const n = Number(rate);
    if (Number.isNaN(n)) return '--';
    const pct = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(3)}%`;
  };

  const fmtUsd = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return '--';
    return `$${n.toFixed(2)}`;
  };

  const formatCountdown = (ms?: number | null): string => {
    if (!ms) return '--';
    const diff = ms - Date.now();
    if (diff <= 0) return '⚡ NOW';
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m`;
    return `${Math.floor(mins / 60)}h${mins % 60 > 0 ? (mins % 60) + 'm' : ''}`;
  };

  const pnlColor = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return 'var(--text-muted)';
    return n >= 0 ? 'var(--green)' : 'var(--red)';
  };

  const modeConfig = (mode?: string) => {
    const m = (mode || '').toLowerCase();
    if (m === 'cherry_pick') return { emoji: '🍒', label: t.cherry_pick, color: '#f97316', bg: 'rgba(249,115,22,0.1)', border: 'rgba(249,115,22,0.35)' };
    if (m === 'pot') return { emoji: '🍯', label: t.pot, color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.4)' };
    if (m === 'nutcracker') return { emoji: '🔨🥜', label: t.nutcracker, color: '#a855f7', bg: 'rgba(168,85,247,0.08)', border: 'rgba(168,85,247,0.35)' };
    return { emoji: '🤝', label: t.hold, color: '#22c55e', bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.35)' };
  };

  const tierConfig = (tier?: string | null) => {
    const k = (tier || '').toLowerCase();
    if (k === 'top')     return { emoji: '🏆', label: t.tierTop,     color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' };
    if (k === 'medium')  return { emoji: '📊', label: t.tierMedium,  color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' };
    if (k === 'bad')     return { emoji: '⚠️', label: t.tierBad,     color: '#ef4444', bg: 'rgba(239,68,68,0.12)' };
    if (k === 'adverse') return { emoji: '',   label: t.tierAdverse, color: '#6b7280', bg: 'rgba(107,114,128,0.12)' };
    return null;
  };

  const fundingCountdownStyle = (ms?: number | null): React.CSSProperties => {
    if (!ms) return { color: 'var(--text-muted)' };
    const diff = ms - Date.now();
    if (diff <= 0) return { color: '#10b981', fontWeight: 700 };
    const mins = diff / 60000;
    if (mins < 2) return { color: '#ef4444', fontWeight: 700, animation: 'funding-blink 0.8s ease-in-out infinite' };
    if (mins < 5) return { color: '#f97316', fontWeight: 600 };
    if (mins < 15) return { color: '#eab308', fontWeight: 500 };
    return { color: '#10b981' };
  };

  const getTargetProgress = (p: PositionRow) => {
    const pnl = num(p.unrealized_pnl_pct);
    const target = num(p.profit_target_pct);
    if (pnl == null || target == null || target === 0) return null;
    const progress = Math.max(0, Math.min(100, (pnl / target) * 100));
    return { pnl, target, progress, remaining: target - pnl };
  };

  const getSizeUsd = (p: PositionRow): string => {
    const price = num(p.entry_price_long);
    const qty = num(p.long_qty);
    if (price == null || qty == null) return '--';
    return '$' + (qty * price).toLocaleString('en-US', { maximumFractionDigits: 0 });
  };

  // ── Render: empty state ─────────────────────────────────────
  if (positions.length === 0) {
    return (
      <div className="card" style={{ position: 'relative' }}>
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 2,
          background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent)',
          borderRadius: '14px 14px 0 0', zIndex: 1, pointerEvents: 'none',
        }} />
        <div className="card-header px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="nx-section-header__icon" style={{ background: 'rgba(6,182,212,0.08)', borderColor: 'rgba(6,182,212,0.12)' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
          </div>
          {t.activePositions}
        </div>
        <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          {t.noOpenPositions}
        </div>
      </div>
    );
  }

  // ── Render: trade cards ─────────────────────────────────────
  return (
    <div className="card" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent)',
        borderRadius: '14px 14px 0 0', zIndex: 1, pointerEvents: 'none',
      }} />
      <div className="card-header px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div className="nx-section-header__icon" style={{ background: 'rgba(6,182,212,0.08)', borderColor: 'rgba(6,182,212,0.12)' }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
          </svg>
        </div>
        {t.activePositions}
        <span className="xcard-live" style={{ marginLeft: 2 }}>
          <span className="xcard-live-dot" />LIVE
        </span>
        <span className="nx-section-badge" style={{ marginLeft: 'auto' }}>
          {positions.length} position{positions.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {positions.map((p) => {
          const mode = modeConfig(p.mode);
          const tier = tierConfig(p.entry_tier);
          const progress = getTargetProgress(p);
          const pnlVal = num(p.unrealized_pnl_pct);
          const isProfit = pnlVal != null && pnlVal >= 0;
          const isExpanded = expandedId === p.id;

          return (
            <div
              key={p.id}
              className={`active-trade-card nx-pos-card ${isProfit ? 'nx-pos-card--profit' : 'nx-pos-card--loss'}`}
              style={{
                borderRadius: 14,
                overflow: 'hidden',
                animationDelay: `${positions.indexOf(p) * 80}ms`,
              }}
            >
              {/* ── Top accent line ── */}
              <div style={{
                height: 2,
                background: isProfit
                  ? 'linear-gradient(90deg, transparent, rgba(16,185,129,0.5), transparent)'
                  : 'linear-gradient(90deg, transparent, rgba(239,68,68,0.5), transparent)',
              }} />

              {/* ── Main card content (clickable) ── */}
              <div
                onClick={() => toggleExpand(p.id)}
                style={{ cursor: 'pointer', padding: '14px 16px 10px' }}
                title={t.clickRowForDetails}
              >
                {/* ── Row 1: Symbol + Mode/Tier + PnL ── */}
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="nx-pos-symbol">
                        {p.symbol.replace('/USDT:USDT', '')}
                      </span>
                      <span className="nx-pos-exchange-tag">
                        {p.long_exchange?.toUpperCase()} ↔ {p.short_exchange?.toUpperCase()}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
                      <span className="nx-pos-mode-badge" style={{
                        color: mode.color, background: mode.bg, borderColor: mode.border,
                      }}>
                        {mode.emoji} {mode.label}
                      </span>
                      {tier && (
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', gap: 2,
                          fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                          padding: '1px 6px', borderRadius: 4,
                          color: tier.color, background: tier.bg,
                          border: `1px solid ${tier.color}44`,
                        }}>
                          {tier.emoji} {tier.label}
                        </span>
                      )}
                      <span style={{
                        fontSize: 9, fontWeight: 600, color: 'var(--text-muted)',
                        textTransform: 'uppercase', letterSpacing: '0.06em',
                      }}>
                        {p.state}
                      </span>
                    </div>
                  </div>

                  {/* Right: PnL big number */}
                  <div style={{ textAlign: 'end', display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                    <span className={`mono nx-pos-pnl ${isProfit ? 'nx-pos-pnl--positive' : 'nx-pos-pnl--negative'}`} style={{
                      fontSize: '1.5rem', fontWeight: 800,
                      lineHeight: 1.1,
                    }}>
                      {fmtPct(p.unrealized_pnl_pct)}
                    </span>
                    <span className="mono" style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 2 }}>
                      {getSizeUsd(p)}
                    </span>
                  </div>
                </div>

                {/* ── Row 2: Target progress bar ── */}
                {progress && (
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
                      <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontWeight: 500 }}>
                        {t.pdTarget}
                      </span>
                      <span className="mono" style={{
                        fontSize: '0.68rem', fontWeight: 600,
                        color: progress.remaining <= 0 ? 'var(--green)' : '#eab308',
                      }}>
                        {progress.remaining <= 0 ? '✅ ' + t.pdTargetReached : `${progress.remaining.toFixed(3)}% ${t.pdToTarget}`}
                      </span>
                    </div>
                    <div className="nx-pos-target-bar">
                      <div className={`nx-pos-target-fill ${progress.progress >= 100 ? 'nx-pos-target-fill--complete' : progress.progress >= 60 ? 'nx-pos-target-fill--progress' : 'nx-pos-target-fill--early'}`} style={{
                        width: `${Math.min(100, progress.progress)}%`,
                      }} />
                    </div>
                  </div>
                )}

                {/* ── Row 3: Stats grid ── */}
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: '0 4px', fontSize: '0.72rem',
                }}>
                  <div style={{ textAlign: 'center' }}>
                    <div className="nx-pos-stat-label">
                      {t.pricePnl}
                    </div>
                    <div className="nx-pos-stat-value" style={{ color: pnlColor(p.price_pnl_pct) }}>
                      {fmtPct(p.price_pnl_pct)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div className="nx-pos-stat-label">
                      {t.fundingNetDetail}
                    </div>
                    <div className="nx-pos-stat-value" style={{ color: pnlColor(p.funding_pnl_pct) }}>
                      {fmtPct(p.funding_pnl_pct)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div className="nx-pos-stat-label">
                      {t.colEntryPct}
                    </div>
                    <div className="nx-pos-stat-value" style={{ color: 'var(--text-primary)' }}>
                      {fmtPct(p.entry_edge_pct)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div className="nx-pos-stat-label">
                      {t.nextPayout}
                    </div>
                    <div className="nx-pos-stat-value" style={{ ...fundingCountdownStyle(p.next_funding_ms) }}>
                      {formatCountdown(p.next_funding_ms)}
                    </div>
                  </div>
                </div>

                {/* ── Row 4: Mini info bar ── */}
                <div className="nx-pos-info-bar">
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <span>
                      {t.colFundPct}: <span className="mono" style={{ color: 'var(--text-secondary)' }}>
                        {fmtFunding(p.current_long_rate)}/{fmtFunding(p.current_short_rate)}
                      </span>
                    </span>
                    <span>
                      💰 {p.funding_collections ?? 0}× ({fmtUsd(p.funding_collected_usd)})
                    </span>
                    {(() => {
                      const isCherryPick = (p.mode || '').toLowerCase() === 'cherry_pick';
                      const pendingUsd = isCherryPick
                        ? num(p.pending_income_usd)
                        : num(p.pending_net_usd);
                      if (pendingUsd == null || Math.abs(pendingUsd) < 0.001) return null;
                      const pendingPct = isCherryPick
                        ? num(p.pending_income_pct)
                        : num(p.pending_net_pct);
                      const isPositive = pendingUsd >= 0;
                      return (
                        <span style={{
                          color: isPositive ? 'var(--green)' : 'var(--red)',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 600,
                        }}>
                          ⏳ {isPositive ? '+' : ''}{pendingPct != null ? pendingPct.toFixed(3) + '%' : ''}
                          {' '}(~{isPositive ? '+' : ''}${Math.abs(pendingUsd).toFixed(2)})
                        </span>
                      );
                    })()}
                  </div>
                  <span style={{
                    fontSize: 14, color: 'var(--text-muted)', opacity: 0.4,
                    transition: 'transform 0.2s ease',
                    transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                  }}>
                    ▾
                  </span>
                </div>
              </div>

              {/* ── Expanded detail card ── */}
              {isExpanded && (
                <div style={{
                  borderTop: '1px solid rgba(148,163,184,0.08)',
                  animation: 'fadeSlideDown 0.2s ease-out',
                }}>
                  <PositionDetailCard position={p} onClose={() => setExpandedId(null)} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default PositionsTable;
