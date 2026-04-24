import React from 'react';
import { useSettings } from '../context/SettingsContext';
import {
  parseNum,
  formatPct,
  formatFundingRateN,
  formatUsd,
  formatPrice,
  formatQty,
  formatDate,
  formatCountdown,
  pnlColor,
} from '../utils/format';
import ExecutionTimeline, { TimelineEvent } from './ExecutionTimeline';

export interface PositionDetail {
  // Quantity per leg (token units)
  long_qty?: string | null;
  short_qty?: string | null;
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
  // Lifecycle
  opened_at?: string | null;
  next_funding_ms?: number | null;
  min_interval_hours?: number;
  state?: string;
}

interface PositionDetailCardProps {
  position: PositionDetail;
  onClose: () => void;
}

const PositionDetailCard: React.FC<PositionDetailCardProps> = ({ position, onClose }) => {
  const { t } = useSettings();

  // ── Computed deltas ─────────────────────────────────────────
  const entryFSpread = parseNum(position.entry_edge_pct);
  const liveFSpread = parseNum(position.current_spread_pct);
  const fSpreadDelta = (entryFSpread != null && liveFSpread != null)
    ? (liveFSpread - entryFSpread).toFixed(4) : null;

  const totalPnl = parseNum(position.unrealized_pnl_pct);
  const profitTarget = parseNum(position.profit_target_pct);
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

  const fundingCollections = position.funding_collections ?? 0;
  const confidenceFrom = (v: number): number => Math.max(0, Math.min(100, Math.round(v)));
  const targetDistance = targetMove != null ? Math.abs(Number(targetMove)) : null;
  const timelineEvents: TimelineEvent[] = [
    {
      id: 'entry',
      label: t.tlPositionOpened,
      detail: `${t.tlEntrySpreadLabel} ${formatPct(position.entry_edge_pct, 3)} | ${t.tlBasisLabel} ${formatPct(position.entry_basis_pct, 3)}`,
      timeLabel: formatDate(position.opened_at),
      confidence: confidenceFrom(position.opened_at ? 90 : 68),
      status: 'done',
    },
    {
      id: 'mark',
      label: t.tlMarkToMarket,
      detail: `${t.tlCurrentSpreadLabel} ${formatPct(position.current_spread_pct, 3)} | ${t.tlPricePnlLabel} ${formatPct(position.price_pnl_pct, 3)}`,
      confidence: confidenceFrom(62 + Math.min(24, Math.abs(Number(position.current_spread_pct ?? 0)) * 1200)),
      status: 'live',
    },
    {
      id: 'funding',
      label: t.tlFundingCollection,
      detail:
        fundingCollections > 0
          ? `${fundingCollections} ${t.tlCollectionsNet} ${formatUsd(position.funding_collected_usd)}`
          : `${t.tlNextWindow} ${formatCountdown(position.next_funding_ms, position.min_interval_hours)}`,
      confidence: confidenceFrom(fundingCollections > 0 ? 86 : 72),
      status: fundingCollections > 0 ? 'done' : 'live',
    },
    {
      id: 'target',
      label: t.tlProfitTarget,
      detail:
        targetMove != null
          ? Number(targetMove) <= 0
            ? t.tlTargetReached
            : `${Number(targetMove).toFixed(3)}% ${t.tlRemaining}`
          : t.tlTargetUnavailable,
      confidence:
        targetDistance == null
          ? 52
          : confidenceFrom(Number(targetMove) <= 0 ? 93 : 68 + Math.max(0, 20 - targetDistance * 8)),
      status:
        targetMove == null ? 'pending' : Number(targetMove) <= 0 ? 'done' : 'live',
    },
    {
      id: 'state',
      label: t.tlExecutionState,
      detail: position.state ? position.state.toUpperCase() : t.tlActive,
      confidence: confidenceFrom(position.state ? 80 : 60),
      status: 'live',
    },
  ];

  return (
    <div style={cardStyle}>
      <button style={closeBtn} onClick={onClose} title="Close">✕</button>

      {/* ── PnL Summary ── */}
      <div style={pnlBarStyle}>
        <span style={{ fontWeight: 700, color: pnlColor(position.unrealized_pnl_pct), fontSize: '1rem' }}>
          {formatPct(position.unrealized_pnl_pct, 3)}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.pricePnl}: <span style={{ color: pnlColor(position.price_pnl_pct) }}>{formatPct(position.price_pnl_pct, 3)}</span>
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.fundingNetDetail}: <span style={{ color: 'var(--green)' }}>{formatPct(position.funding_pnl_pct, 3)}</span>
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
          {t.feesDetail}: <span style={{ color: 'var(--red)' }}>-{formatPct(position.fees_pct, 3)}</span>
        </span>
        {targetMove != null && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem', marginInlineStart: 'auto' }}>
            {t.pdTarget}: <span style={{ color: Number(targetMove) <= 0 ? 'var(--green)' : '#eab308', fontWeight: 600 }}>
              {Number(targetMove) <= 0 ? '✅' : `${Number(targetMove).toFixed(3)}%`}
            </span>
          </span>
        )}
      </div>

      {/* ── Size Section: qty + USD notional per leg ── */}
      {(position.long_qty != null || position.short_qty != null) && (() => {
        const calc = (qty?: string | null, price?: string | null): number | null => {
          if (qty == null || price == null) return null;
          const q = Number(qty);
          const p = Number(price);
          if (!Number.isFinite(q) || !Number.isFinite(p)) return null;
          return q * p;
        };
        const lQty = position.long_qty;
        const sQty = position.short_qty;
        const lEntryNot = calc(lQty, position.entry_price_long);
        const sEntryNot = calc(sQty, position.entry_price_short);
        const lLiveNot  = calc(lQty, position.live_price_long ?? position.entry_price_long);
        const sLiveNot  = calc(sQty, position.live_price_short ?? position.entry_price_short);
        return (
          <div style={sectionStyle}>
            <div style={sectionTitleStyle}>🪙 {t.pdSize}</div>
            <div style={gridStyle}>
              <div style={headerCellStyle}>{t.qtyLabel}</div>
              <div style={headerCellStyle}>{t.notionalEntry}</div>
              <div style={headerCellStyle}>{t.notionalExit /* live notional reuses 'exit' label as "current" */}</div>

              {/* Long row */}
              <div style={valueCellStyle}>L: {formatQty(lQty)}</div>
              <div style={valueCellStyle}>L: {formatUsd(lEntryNot)}</div>
              <div style={valueCellStyle}>L: {formatUsd(lLiveNot)}</div>

              {/* Short row */}
              <div style={valueCellStyle}>S: {formatQty(sQty)}</div>
              <div style={valueCellStyle}>S: {formatUsd(sEntryNot)}</div>
              <div style={valueCellStyle}>S: {formatUsd(sLiveNot)}</div>
            </div>
          </div>
        );
      })()}

      {/* ── Prices Section ── */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>📊 {t.pdPrices}</div>
        <div style={gridStyle}>
          <div style={headerCellStyle}>{t.pdEntry}</div>
          <div style={headerCellStyle}>{t.pdLive}</div>
          <div style={headerCellStyle}>{t.pdDelta}</div>

          {/* Long price */}
          <div style={valueCellStyle}>L: {formatPrice(position.entry_price_long)}</div>
          <div style={valueCellStyle}>L: {formatPrice(position.live_price_long)}</div>
          <div style={{ ...valueCellStyle, gridRow: 'span 2', display: 'flex', alignItems: 'center', fontWeight: 700, color: pnlColor(position.price_pnl_pct) }}>
            {formatPct(position.price_pnl_pct, 3)}
          </div>

          {/* Short price */}
          <div style={valueCellStyle}>S: {formatPrice(position.entry_price_short)}</div>
          <div style={valueCellStyle}>S: {formatPrice(position.live_price_short)}</div>
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
          <div style={valueCellStyle}>L: {formatFundingRateN(position.long_funding_rate, 4)}</div>
          <div style={valueCellStyle}>L: {formatFundingRateN(position.current_long_rate, 4)}</div>
          <div style={{ ...valueCellStyle, gridRow: 'span 2', display: 'flex', alignItems: 'center', fontWeight: 600, color: fSpreadDelta ? (Number(fSpreadDelta) >= 0 ? 'var(--green)' : 'var(--red)') : 'inherit' }}>
            {fSpreadDelta != null ? `${Number(fSpreadDelta) >= 0 ? '+' : ''}${Number(fSpreadDelta).toFixed(3)}%` : '--'}
          </div>

          {/* Short funding */}
          <div style={valueCellStyle}>S: {formatFundingRateN(position.short_funding_rate, 4)}</div>
          <div style={valueCellStyle}>S: {formatFundingRateN(position.current_short_rate, 4)}</div>
        </div>
        {/* Spread row */}
        <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          <span>{t.pdEntrySpread}: {formatPct(position.entry_edge_pct, 3)}</span>
          <span>{t.pdLiveSpread}: {formatPct(position.current_spread_pct, 3)}</span>
          <span>{t.pdCollections}: {position.funding_collections ?? 0} ({formatUsd(position.funding_collected_usd)})</span>
        </div>
      </div>

      {/* ── Basis Section ── */}
      <div style={{ ...sectionStyle, marginBottom: 0 }}>
        <div style={sectionTitleStyle}>📐 {t.pdBasis}</div>
        <div style={gridStyle}>
          <div style={headerCellStyle}>{t.pdEntry}</div>
          <div style={headerCellStyle}>{t.pdLive}</div>
          <div style={headerCellStyle}>{t.pdTarget}</div>

          <div style={valueCellStyle}>{formatPct(position.entry_basis_pct, 3)}</div>
          <div style={valueCellStyle}>{formatPct(position.current_basis_pct, 3)}</div>
          <div style={{ ...valueCellStyle, fontWeight: 600, color: targetMove != null ? (Number(targetMove) <= 0 ? 'var(--green)' : '#eab308') : 'inherit' }}>
            {targetMove != null ? (Number(targetMove) <= 0 ? '✅ ' + t.pdTargetReached : `${Number(targetMove).toFixed(3)}% ${t.pdToTarget}`) : '--'}
          </div>
        </div>
      </div>

      <ExecutionTimeline title={t.executionTimeline} events={timelineEvents} />
    </div>
  );
};

export default React.memo(PositionDetailCard);
