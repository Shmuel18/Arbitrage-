import React, { useEffect, useRef } from 'react';
import { useSettings } from '../context/SettingsContext';
import { Trade } from '../types';
import {
  formatUsd,
  formatFundingRateN,
  formatDate,
  formatDuration,
  pnlColor,
  ModeBadge,
  TierBadge,
} from '../utils/format';
import ExecutionTimeline, { TimelineEvent } from './ExecutionTimeline';

interface TradeDetailModalProps {
  trade: Trade;
  onClose: () => void;
}

const TradeDetailModal: React.FC<TradeDetailModalProps> = ({ trade, onClose }) => {
  const { t } = useSettings();
  const dialogRef = useRef<HTMLDivElement>(null);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  // Focus trap: keep focus inside the modal
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = dialog.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length > 0) focusable[0].focus();

    const trapFocus = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', trapFocus);
    return () => document.removeEventListener('keydown', trapFocus);
  }, []);

  // ── PnL values ──────────────────────────────────────────────────
  const feesNum = trade.fees_paid_total != null ? -Math.abs(Number(trade.fees_paid_total)) : null;
  const pricePnl  = trade.price_pnl ?? null;
  const fundingNet = trade.funding_net ?? null;
  const totalPnl  = trade.total_pnl ?? null;

  // ── Section styles ──────────────────────────────────────────────
  const sectionTitle: React.CSSProperties = {
    fontSize: 10, fontWeight: 800, letterSpacing: '0.12em', textTransform: 'uppercase',
    color: 'var(--text-muted)', marginBottom: 12, marginTop: 0,
    paddingBottom: 6, borderBottom: '1px solid var(--card-border)',
  };

  const rowStyle: React.CSSProperties = {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '6px 0', borderBottom: '1px solid var(--card-border)',
  };

  const labelStyle: React.CSSProperties = {
    color: 'var(--text-secondary)', fontSize: 12,
  };

  const valueStyle: React.CSSProperties = {
    fontSize: 13, fontWeight: 600,
    fontFamily: 'var(--font-mono, monospace)',
    fontVariantNumeric: 'tabular-nums',
    color: 'var(--text-primary)',
  };

  const totalRowStyle: React.CSSProperties = {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 0 4px', borderTop: '2px solid var(--card-border)', marginTop: 8,
  };

  const openedAt = trade.opened_at || trade.open_time || null;
  const closedAt = trade.closed_at || trade.close_time || null;
  const fundingCollections = trade.funding_collections ?? 0;
  const confidenceFrom = (v: number): number => Math.max(0, Math.min(100, Math.round(v)));
  const spreadAbs = Math.abs(Number(trade.entry_spread ?? 0));
  const totalPnlAbs = Math.abs(Number(totalPnl ?? 0));

  const timelineEvents: TimelineEvent[] = [
    {
      id: 'entry',
      label: 'Execution Started',
      detail: `${trade.long_exchange?.toUpperCase()} / ${trade.short_exchange?.toUpperCase()} pair opened`,
      timeLabel: formatDate(openedAt),
      confidence: confidenceFrom(openedAt ? 92 : 70),
      status: 'done',
    },
    {
      id: 'mark',
      label: 'Spread Captured',
      detail: `Entry edge ${formatFundingRateN(trade.entry_spread, 4)} | Basis ${formatFundingRateN(trade.entry_basis_pct, 4)}`,
      confidence: confidenceFrom(55 + Math.min(40, spreadAbs * 2000)),
      status: 'done',
    },
    {
      id: 'funding',
      label: 'Funding Settlement',
      detail:
        fundingCollections > 0
          ? `${fundingCollections} collection(s), net ${formatUsd(trade.funding_collected_usd)}`
          : 'No funding settlement recorded',
      confidence: confidenceFrom(fundingCollections > 0 ? 88 : trade.status === 'closed' ? 50 : 72),
      status: fundingCollections > 0 ? 'done' : trade.status === 'closed' ? 'pending' : 'live',
    },
    {
      id: 'exit',
      label: 'Exit & Attribution',
      detail: trade.exit_reason || 'Awaiting exit trigger',
      timeLabel: closedAt ? formatDate(closedAt) : undefined,
      confidence: confidenceFrom(closedAt ? 90 : 68),
      status: trade.status === 'closed' ? 'done' : 'live',
    },
    {
      id: 'final',
      label: 'Net Result',
      detail: `${formatUsd(totalPnl)} total | hold ${formatDuration(trade.hold_minutes)}`,
      confidence: confidenceFrom(trade.status === 'closed' ? 70 + Math.min(25, totalPnlAbs * 2.5) : 55),
      status: trade.status === 'closed' ? 'done' : 'pending',
    },
  ];

  return (
    <>
      <div
        onClick={onClose}
        aria-hidden="true"
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          zIndex: 1000, backdropFilter: 'blur(4px)',
        }}
      />

      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${t.tradeDetail} — ${trade.symbol}`}
        style={{
        position: 'fixed', top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        zIndex: 1001,
        width: 'min(600px, 95vw)',
        maxHeight: '90vh',
        overflowY: 'auto',
        background: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
        borderRadius: 16,
        boxShadow: 'var(--shadow-xl)',
        padding: '24px 28px',
        overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 3,
          background: 'linear-gradient(90deg, #3b82f6, #8b5cf6, #ec4899)',
        }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
              <span style={{ fontSize: 22, fontWeight: 900, letterSpacing: '-0.02em', color: 'var(--accent)' }}>
                {trade.symbol}
              </span>
              <ModeBadge mode={trade.mode} t={t} />
              <TierBadge tier={trade.entry_tier} t={t} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: 13, fontWeight: 500 }}>
              <span className="mono">{trade.long_exchange?.toUpperCase()}</span>
              <span style={{ opacity: 0.3 }}>/</span>
              <span className="mono">{trade.short_exchange?.toUpperCase()}</span>
              <span style={{ color: 'var(--card-border)', margin: '0 4px' }}>|</span>
              <span>{formatDuration(trade.hold_minutes)}</span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{
              background: trade.status === 'closed' ? 'rgba(16,185,129,0.1)' : 'rgba(14,165,233,0.1)',
              color: trade.status === 'closed' ? 'var(--green)' : 'var(--blue)',
              borderRadius: 6, padding: '4px 10px', fontSize: 11, fontWeight: 800,
              textTransform: 'uppercase', letterSpacing: '0.05em',
            }}>
              {trade.status === 'closed' ? t.statusClosed : t.statusActive}
            </span>
            <button
              onClick={onClose}
              aria-label="Close dialog"
              style={{
                background: 'rgba(148,163,184,0.1)', border: 'none', cursor: 'pointer',
                color: 'var(--text-muted)', fontSize: 18, borderRadius: '50%',
                width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >×</button>
          </div>
        </div>

        {/* ── Times & Prices ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, marginBottom: 24 }}>
          {/* LONG SIDE */}
          <div>
            <p style={sectionTitle}>{trade.long_exchange?.toUpperCase()} ({t.long})</p>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.entryPriceLong}</span>
              <span style={valueStyle}>{formatUsd(trade.entry_price_long, 5)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingAtEntry}</span>
              <span style={valueStyle}>{formatFundingRateN(trade.long_funding_rate, 4)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.exitPriceLong}</span>
              <span style={valueStyle}>{formatUsd(trade.exit_price_long, 5)}</span>
            </div>
            <div style={{ ...rowStyle, borderBottom: 'none' }}>
              <span style={labelStyle}>{t.openedAt}</span>
              <span style={{ ...valueStyle, fontWeight: 400 }}>{formatDate(trade.opened_at || trade.open_time)}</span>
            </div>
          </div>

          {/* SHORT SIDE */}
          <div>
            <p style={sectionTitle}>{trade.short_exchange?.toUpperCase()} ({t.short})</p>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.entryPriceShort}</span>
              <span style={valueStyle}>{formatUsd(trade.entry_price_short, 5)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingAtEntry}</span>
              <span style={valueStyle}>{formatFundingRateN(trade.short_funding_rate, 4)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.exitPriceShort}</span>
              <span style={valueStyle}>{formatUsd(trade.exit_price_short, 5)}</span>
            </div>
            <div style={{ ...rowStyle, borderBottom: 'none' }}>
              <span style={labelStyle}>{t.closedAt}</span>
              <span style={{ ...valueStyle, fontWeight: 400 }}>{formatDate(trade.closed_at || trade.close_time)}</span>
            </div>
          </div>
        </div>

        {/* ── Financials ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 24, marginBottom: 12 }}>
          {/* PNL Breakdown */}
          <div style={{ background: 'rgba(59,130,246,0.03)', borderRadius: 12, padding: '16px 20px', border: '1px solid var(--card-border)' }}>
            <p style={{ ...sectionTitle, borderBottom: 'none' }}>{t.tradeDetailPnl}</p>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.pricePnl}</span>
              <span style={{ ...valueStyle, color: pnlColor(pricePnl) }}>{formatUsd(pricePnl)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingNetDetail}</span>
              <span style={{ ...valueStyle, color: pnlColor(fundingNet) }}>{formatUsd(fundingNet)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.feesDetail}</span>
              <span style={{ ...valueStyle, color: 'var(--red)' }}>{formatUsd(feesNum)}</span>
            </div>
            <div style={totalRowStyle}>
              <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>{t.totalNetPnl}</span>
              <span style={{ fontSize: 18, fontWeight: 900, color: pnlColor(totalPnl) }}>{formatUsd(totalPnl)}</span>
            </div>
          </div>

          {/* Stats */}
          <div style={{ padding: '4px 0' }}>
            <p style={{ ...sectionTitle, borderBottom: 'none' }}>{t.tradeDetailFunding}</p>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.collectionsCount}</span>
              <span style={valueStyle}>{trade.funding_collections ?? '--'}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingCollectedUsd}</span>
              <span style={{ ...valueStyle, color: 'var(--green)' }}>{formatUsd(trade.funding_collected_usd)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.entryEdge}</span>
              <span style={valueStyle}>{formatFundingRateN(trade.entry_spread, 4)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.entryBasis}</span>
              <span style={valueStyle}>{formatFundingRateN(trade.entry_basis_pct, 4)}</span>
            </div>
          </div>
        </div>

        <ExecutionTimeline title="Execution Confidence Timeline" events={timelineEvents} />

        {/* ── Exit Reason ── */}
        {trade.exit_reason && (
          <div style={{ marginTop: 12 }}>
            <p style={{ ...sectionTitle, borderBottom: 'none', marginBottom: 8 }}>{t.exitReasonLabel}</p>
            <div style={{
              background: 'var(--tag-bg, rgba(148,163,184,0.1))',
              borderRadius: 8, padding: '10px 14px', fontSize: 12,
              color: 'var(--text-secondary)', fontStyle: 'italic',
              borderLeft: '3px solid var(--accent)',
            }}>
              "{trade.exit_reason}"
            </div>
          </div>
        )}
      </div>
    </>
  );
};

export default React.memo(TradeDetailModal);
