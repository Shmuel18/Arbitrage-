import React from 'react';
import { useSettings } from '../context/SettingsContext';

export interface PositionDetail {
  // Entry prices
  entry_price_long?: string | null;
  entry_price_short?: string | null;
  // Live prices
  live_price_long?: string | null;
  live_price_short?: string | null;
  // Funding at entry
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  // Live funding
  current_long_rate?: string | null;
  current_short_rate?: string | null;
  // Spreads
  entry_edge_pct?: string | null;
  immediate_spread_pct?: string | null;
  current_spread_pct?: string | null;
  // Basis
  entry_basis_pct?: string | null;
  current_basis_pct?: string | null;
  // Price spread at entry
  price_spread_pct?: string | null;
  // PnL breakdown
  unrealized_pnl_pct?: string | null;
  price_pnl_pct?: string | null;
  funding_pnl_pct?: string | null;
  fees_pct?: string | null;
  // Funding collections
  funding_collected_usd?: string | null;
  fees_paid_total?: string | null;
  funding_collections?: number | null;
  // Target
  profit_target_pct?: string | null;
}

interface PositionDetailCardProps {
  position: PositionDetail;
  onClose: () => void;
}

const PositionDetailCard: React.FC<PositionDetailCardProps> = ({ position, onClose }) => {
  const { t } = useSettings();

  // ── Helpers ──────────────────────────────────────────────────
  const num = (v?: string | null): number | null => {
    if (v == null || v === '') return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  };

  const fmtPrice = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return '--';
    if (n < 0.01) return n.toPrecision(4);
    if (n < 1) return n.toFixed(4);
    return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  };

  const fmtPct = (v?: string | null, decimals = 4): string => {
    const n = num(v);
    if (n == null) return '--';
    return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`;
  };

  const fmtFunding = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return '--';
    const pct = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const fmtUsd = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return '--';
    return `$${n.toFixed(2)}`;
  };

  const pnlColor = (v?: string | null): string => {
    const n = num(v);
    if (n == null) return 'var(--text-muted)';
    return n >= 0 ? 'var(--green)' : 'var(--red)';
  };

  // ── Computed deltas ─────────────────────────────────────────
  const entryFSpread = num(position.entry_edge_pct);
  const liveFSpread = num(position.current_spread_pct);
  const fSpreadDelta = (entryFSpread != null && liveFSpread != null)
    ? (liveFSpread - entryFSpread).toFixed(4) : null;

  const totalPnl = num(position.unrealized_pnl_pct);
  const profitTarget = num(position.profit_target_pct);
  const targetMove = (totalPnl != null && profitTarget != null)
    ? (profitTarget - totalPnl).toFixed(4) : null;

  // ── Styles ──────────────────────────────────────────────────
  const cardStyle: React.CSSProperties = {
    background: 'var(--card-bg)',
    border: '1px solid var(--card-border)',
    borderRadius: 10,
    padding: '12px 16px',
    marginTop: 4,
    position: 'relative',
    animation: 'fadeSlideDown 0.2s ease-out',
  };

  const sectionStyle: React.CSSProperties = {
    marginBottom: 10,
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: 10,
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: 'var(--text-muted)',
    marginBottom: 4,
    borderBottom: '1px solid var(--card-border)',
    paddingBottom: 2,
  };

  const gridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr 1fr',
    gap: '2px 12px',
    fontSize: '0.78rem',
  };

  const headerCellStyle: React.CSSProperties = {
    fontSize: 9,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    color: 'var(--text-muted)',
    paddingBottom: 1,
  };

  const valueCellStyle: React.CSSProperties = {
    fontFamily: 'monospace',
    fontSize: '0.78rem',
  };

  const closeBtn: React.CSSProperties = {
    position: 'absolute',
    top: 6,
    right: 10,
    background: 'transparent',
    border: 'none',
    color: 'var(--text-muted)',
    cursor: 'pointer',
    fontSize: 16,
    lineHeight: 1,
    padding: 2,
  };

  // ── PnL Summary Bar ────────────────────────────────────────
  const pnlBarStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '6px 10px',
    borderRadius: 6,
    background: totalPnl != null
      ? (totalPnl >= 0 ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)')
      : 'rgba(148,163,184,0.06)',
    marginBottom: 10,
    fontSize: '0.82rem',
    fontFamily: 'monospace',
  };

  return (
    <div style={cardStyle}>
      <button style={closeBtn} onClick={onClose} title="Close">✕</button>

      {/* ── PnL Summary ── */}
      <div style={pnlBarStyle}>
        <span style={{ fontWeight: 700, color: pnlColor(position.unrealized_pnl_pct), fontSize: '1rem' }}>
          {fmtPct(position.unrealized_pnl_pct, 3)}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.pricePnl}: <span style={{ color: pnlColor(position.price_pnl_pct) }}>{fmtPct(position.price_pnl_pct, 3)}</span>
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.fundingNetDetail}: <span style={{ color: 'var(--green)' }}>{fmtPct(position.funding_pnl_pct, 3)}</span>
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.feesDetail}: <span style={{ color: 'var(--red)' }}>-{fmtPct(position.fees_pct, 3)}</span>
        </span>
        {targetMove != null && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem', marginLeft: 'auto' }}>
            {t.pdTarget}: <span style={{ color: Number(targetMove) <= 0 ? 'var(--green)' : '#eab308', fontWeight: 600 }}>
              {Number(targetMove) <= 0 ? '✅' : `${Number(targetMove).toFixed(3)}%`}
            </span>
          </span>
        )}
      </div>

      {/* ── Prices Section ── */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>📊 {t.pdPrices}</div>
        <div style={gridStyle}>
          <div style={headerCellStyle}>{t.pdEntry}</div>
          <div style={headerCellStyle}>{t.pdLive}</div>
          <div style={headerCellStyle}>{t.pdDelta}</div>

          {/* Long price */}
          <div style={valueCellStyle}>L: {fmtPrice(position.entry_price_long)}</div>
          <div style={valueCellStyle}>L: {fmtPrice(position.live_price_long)}</div>
          <div style={{ ...valueCellStyle, gridRow: 'span 2', display: 'flex', alignItems: 'center', fontWeight: 700, color: pnlColor(position.price_pnl_pct) }}>
            {fmtPct(position.price_pnl_pct, 3)}
          </div>

          {/* Short price */}
          <div style={valueCellStyle}>S: {fmtPrice(position.entry_price_short)}</div>
          <div style={valueCellStyle}>S: {fmtPrice(position.live_price_short)}</div>
        </div>
      </div>

      {/* ── Funding Section ── */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>💰 {t.pdFunding}</div>
        <div style={gridStyle}>
          <div style={headerCellStyle}>{t.pdEntry}</div>
          <div style={headerCellStyle}>{t.pdLive}</div>
          <div style={headerCellStyle}>{t.pdDelta}</div>

          {/* Long funding */}
          <div style={valueCellStyle}>L: {fmtFunding(position.long_funding_rate)}</div>
          <div style={valueCellStyle}>L: {fmtFunding(position.current_long_rate)}</div>
          <div style={{ ...valueCellStyle, gridRow: 'span 2', display: 'flex', alignItems: 'center', fontWeight: 600, color: fSpreadDelta ? (Number(fSpreadDelta) >= 0 ? 'var(--green)' : 'var(--red)') : 'inherit' }}>
            {fSpreadDelta != null ? `${Number(fSpreadDelta) >= 0 ? '+' : ''}${Number(fSpreadDelta).toFixed(3)}%` : '--'}
          </div>

          {/* Short funding */}
          <div style={valueCellStyle}>S: {fmtFunding(position.short_funding_rate)}</div>
          <div style={valueCellStyle}>S: {fmtFunding(position.current_short_rate)}</div>
        </div>
        {/* Spread row */}
        <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          <span>{t.pdEntrySpread}: {fmtPct(position.entry_edge_pct, 3)}</span>
          <span>{t.pdLiveSpread}: {fmtPct(position.current_spread_pct, 3)}</span>
          <span>{t.pdCollections}: {position.funding_collections ?? 0} ({fmtUsd(position.funding_collected_usd)})</span>
        </div>
      </div>

      {/* ── Basis Section ── */}
      <div style={{ ...sectionStyle, marginBottom: 0 }}>
        <div style={sectionTitleStyle}>📐 {t.pdBasis}</div>
        <div style={gridStyle}>
          <div style={headerCellStyle}>{t.pdEntry}</div>
          <div style={headerCellStyle}>{t.pdLive}</div>
          <div style={headerCellStyle}>{t.pdTarget}</div>

          <div style={valueCellStyle}>{fmtPct(position.entry_basis_pct, 3)}</div>
          <div style={valueCellStyle}>{fmtPct(position.current_basis_pct, 3)}</div>
          <div style={{ ...valueCellStyle, fontWeight: 600, color: targetMove != null ? (Number(targetMove) <= 0 ? 'var(--green)' : '#eab308') : 'inherit' }}>
            {targetMove != null ? (Number(targetMove) <= 0 ? '✅ ' + t.pdTargetReached : `${Number(targetMove).toFixed(3)}% ${t.pdToTarget}`) : '--'}
          </div>
        </div>
      </div>
    </div>
  );
};

export default PositionDetailCard;
