import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
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
  const fillBasis    = trade.entry_basis_pct != null ? Number(trade.entry_basis_pct) : null;
  const scannerSpread = trade.price_spread_pct != null ? Number(trade.price_spread_pct) : null;
  const basisSlippage = fillBasis != null && scannerSpread != null ? fillBasis - scannerSpread : null;

  // ── Section styles ──────────────────────────────────────────────
  const sectionTitle: React.CSSProperties = {
    fontSize: 10, fontWeight: 800, letterSpacing: '0.12em', textTransform: 'uppercase',
    color: 'var(--text-muted)', marginBottom: 12, marginTop: 0,
    paddingBottom: 6, borderBottom: '1px solid var(--card-border)',
  };

  const rowStyle: React.CSSProperties = {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    gap: 8,
    padding: '6px 0', borderBottom: '1px solid var(--card-border)',
  };

  const labelStyle: React.CSSProperties = {
    color: 'var(--text-secondary)', fontSize: 12,
    flexShrink: 0,
  };

  const valueStyle: React.CSSProperties = {
    fontSize: 13, fontWeight: 600,
    fontFamily: 'var(--font-mono, monospace)',
    fontVariantNumeric: 'tabular-nums',
    color: 'var(--text-primary)',
    textAlign: 'right',
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
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
      label: t.tlExecutionStarted,
      detail: `${trade.long_exchange?.toUpperCase()} / ${trade.short_exchange?.toUpperCase()} ${t.tlPairOpened}`,
      timeLabel: formatDate(openedAt),
      confidence: confidenceFrom(openedAt ? 92 : 70),
      status: 'done',
    },
    {
      id: 'mark',
      label: t.tlSpreadCaptured,
      detail: `${t.tlEntrySpreadLabel} ${formatFundingRateN(trade.entry_spread, 4)} | ${t.tlBasisLabel} ${formatFundingRateN(trade.entry_basis_pct, 4)}`,
      confidence: confidenceFrom(55 + Math.min(40, spreadAbs * 2000)),
      status: 'done',
    },
    {
      id: 'funding',
      label: t.tlFundingSettlement,
      detail:
        fundingCollections > 0
          ? `${fundingCollections} ${t.tlCollectionsNet} ${formatUsd(trade.funding_collected_usd)}`
          : t.tlNoFundingSettlement,
      confidence: confidenceFrom(fundingCollections > 0 ? 88 : trade.status === 'closed' ? 50 : 72),
      status: fundingCollections > 0 ? 'done' : trade.status === 'closed' ? 'pending' : 'live',
    },
    {
      id: 'exit',
      label: t.tlExitAttribution,
      detail: trade.exit_reason || t.tlAwaitingExit,
      timeLabel: closedAt ? formatDate(closedAt) : undefined,
      confidence: confidenceFrom(closedAt ? 90 : 68),
      status: trade.status === 'closed' ? 'done' : 'live',
    },
    {
      id: 'final',
      label: t.tlNetResult,
      detail: `${formatUsd(totalPnl)} ${t.tlTotalLabel} | ${t.tlHoldLabel} ${formatDuration(trade.hold_minutes)}`,
      confidence: confidenceFrom(trade.status === 'closed' ? 70 + Math.min(25, totalPnlAbs * 2.5) : 55),
      status: trade.status === 'closed' ? 'done' : 'pending',
    },
  ];

  return createPortal(
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
          width: 'min(620px, 96vw)',
          maxHeight: '92vh',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--card-bg)',
          border: '1px solid var(--card-border)',
          borderRadius: 16,
          boxShadow: 'var(--shadow-xl)',
          overflow: 'hidden',
          boxSizing: 'border-box',
        }}>
        {/* Sticky gradient bar — stays at top even when scrolling */}
        <div style={{
          flexShrink: 0,
          height: 3,
          background: 'linear-gradient(90deg, #2DB8C4, #1B3A6B, #2DB8C4)',
        }} />
        {/* Scrollable content area */}
        <div style={{ overflowY: 'auto', overflowX: 'hidden', padding: '24px 28px 28px', flex: 1 }}>

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
              aria-label={t.closeDialog}
              style={{
                background: 'rgba(148,163,184,0.1)', border: 'none', cursor: 'pointer',
                color: 'var(--text-muted)', fontSize: 18, borderRadius: '50%',
                width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >×</button>
          </div>
        </div>

        {/* ── Times & Prices ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 20, marginBottom: 24 }}>
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
        {/* Net total highlight */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: totalPnl != null && totalPnl >= 0 ? 'rgba(16,185,129,0.07)' : 'rgba(239,68,68,0.07)',
          border: `1px solid ${totalPnl != null && totalPnl >= 0 ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'}`,
          borderRadius: 10, padding: '10px 16px', marginBottom: 12,
        }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>{t.totalNetPnl}</span>
          <span style={{ fontSize: 20, fontWeight: 900, color: pnlColor(totalPnl) }}>{formatUsd(totalPnl)}</span>
        </div>

        {/* Two equal boxes */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 12 }}>
          {/* PNL Breakdown */}
          <div style={{ background: 'var(--card-bg)', borderRadius: 10, padding: '12px 16px', border: '1px solid var(--card-border)' }}>
            <p style={sectionTitle}>{t.tradeDetailPnl}</p>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.pricePnl}</span>
              <span style={{ ...valueStyle, color: pnlColor(pricePnl) }}>{formatUsd(pricePnl)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingNetDetail}</span>
              <span style={{ ...valueStyle, color: pnlColor(fundingNet) }}>{formatUsd(fundingNet)}</span>
            </div>
            <div style={{ ...rowStyle, borderBottom: 'none' }}>
              <span style={labelStyle}>{t.feesDetail}</span>
              <span style={{ ...valueStyle, color: 'var(--red)' }}>{formatUsd(feesNum)}</span>
            </div>
          </div>

          {/* Stats */}
          <div style={{ background: 'var(--card-bg)', borderRadius: 10, padding: '12px 16px', border: '1px solid var(--card-border)' }}>
            <p style={sectionTitle}>{t.tradeDetailFunding}</p>
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
            <div style={{ ...rowStyle, borderBottom: scannerSpread != null ? undefined : 'none' }}>
              <span style={labelStyle}>{t.entryBasis}</span>
              <span style={{ ...valueStyle, color: fillBasis != null && fillBasis < 0 ? 'var(--green)' : 'var(--text-primary)' }}>
                {formatFundingRateN(fillBasis, 4)}
              </span>
            </div>
            {scannerSpread != null && (
              <div style={{ ...rowStyle, borderBottom: 'none' }}>
                <span style={labelStyle}>{t.scannerSpread}</span>
                <span style={{ ...valueStyle, color: 'var(--text-secondary)' }}>
                  {formatFundingRateN(scannerSpread, 4)}
                  {basisSlippage != null && Math.abs(basisSlippage) > 0.0005 && (
                    <span style={{ fontSize: 10, color: basisSlippage < 0 ? 'var(--red)' : 'var(--green)', marginInlineStart: 4 }}>
                      ({basisSlippage > 0 ? '+' : ''}{(basisSlippage * 100).toFixed(2)}bp)
                    </span>
                  )}
                </span>
              </div>
            )}
          </div>
        </div>

        <ExecutionTimeline title={t.executionTimeline} events={timelineEvents} />

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
        </div>{/* end scrollable */}
      </div>
    </>,
    document.body
  );
};

export default React.memo(TradeDetailModal);
