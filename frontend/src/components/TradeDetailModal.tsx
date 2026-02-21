import React, { useEffect } from 'react';
import { useSettings } from '../context/SettingsContext';
import { Trade } from '../types';

interface TradeDetailModalProps {
  trade: Trade;
  onClose: () => void;
}

const TradeDetailModal: React.FC<TradeDetailModalProps> = ({ trade, onClose }) => {
  const { t } = useSettings();

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  // ── Formatters ──────────────────────────────────────────────────
  const usd = (value?: number | string | null, fractions = 2) => {
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD',
      minimumFractionDigits: fractions,
      maximumFractionDigits: fractions,
    }).format(n);
  };

  const pct = (value?: number | string | null) => {
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    // values like 0.0042 → multiply by 100; values > 1 already percentage
    const p = Math.abs(n) <= 1 ? n * 100 : n;
    return `${p >= 0 ? '+' : ''}${p.toFixed(4)}%`;
  };

  const formatDate = (value?: string | null) => {
    if (!value) return '--';
    try { return new Date(value).toLocaleString(); } catch { return '--'; }
  };

  const formatDuration = (mins?: number | null) => {
    if (mins == null) return '--';
    if (mins < 60) return `${Math.round(mins)}m`;
    const h = Math.floor(mins / 60);
    const m = Math.round(mins % 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  };

  const pnlColor = (v?: number | null) => {
    if (v == null) return 'inherit';
    return v >= 0 ? 'var(--green)' : 'var(--red)';
  };

  const modeBadge = (m?: string | null) => {
    if (!m) return null;
    const label = m.replace('_', ' ').toUpperCase();
    const color = m === 'cherry_pick' ? '#f59e0b' : m === 'hold_mixed' ? '#8b5cf6' : '#22d3ee';
    return (
      <span style={{
        background: color + '22', color, border: `1px solid ${color}55`,
        borderRadius: 4, padding: '1px 8px', fontSize: 11, fontWeight: 700,
        textTransform: 'uppercase', letterSpacing: '0.06em', marginLeft: 8,
      }}>
        {label}
      </span>
    );
  };

  // ── PnL values ──────────────────────────────────────────────────
  const feesNum = trade.fees_paid_total != null ? -Math.abs(Number(trade.fees_paid_total)) : null;
  const pricePnl  = trade.price_pnl ?? null;
  const fundingNet = trade.funding_net ?? null;
  const totalPnl  = trade.total_pnl ?? null;

  // ── Section styles ──────────────────────────────────────────────
  const sectionTitle: React.CSSProperties = {
    fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
    color: 'var(--text-muted)', marginBottom: 10, marginTop: 0,
  };

  const rowStyle: React.CSSProperties = {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '6px 0', borderBottom: '1px solid var(--card-border)',
  };

  const labelStyle: React.CSSProperties = {
    color: 'var(--text-muted)', fontSize: 13,
  };

  const valueStyle: React.CSSProperties = {
    fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-mono, monospace)',
  };

  const totalRowStyle: React.CSSProperties = {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '8px 0 2px', borderTop: '1px solid var(--card-border)', marginTop: 4,
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)',
          zIndex: 1000, backdropFilter: 'blur(3px)',
        }}
      />

      {/* Modal */}
      <div style={{
        position: 'fixed', top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        zIndex: 1001,
        width: 'min(520px, 95vw)',
        maxHeight: '90vh',
        overflowY: 'auto',
        background: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
        borderRadius: 10,
        boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
        padding: '20px 24px',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: '0.04em', color: 'var(--accent)' }}>
              {trade.symbol}
            </span>
            {modeBadge(trade.mode)}
            <span style={{
              background: trade.status === 'closed' ? 'var(--green-muted, #16a34a33)' : 'var(--blue-muted, #0ea5e933)',
              color: trade.status === 'closed' ? 'var(--green)' : 'var(--info, #38bdf8)',
              border: `1px solid ${trade.status === 'closed' ? 'var(--green)' : 'var(--info, #38bdf8)'}55`,
              borderRadius: 4, padding: '1px 8px', fontSize: 11, fontWeight: 700,
              letterSpacing: '0.06em', marginLeft: 4,
            }}>
              {(trade.status ?? 'open').toUpperCase()}
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--text-muted)', fontSize: 20, lineHeight: 1, padding: 4,
            }}
            aria-label="Close"
          >×</button>
        </div>

        <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 20 }}>
          {trade.long_exchange?.toUpperCase()} LONG &nbsp;/&nbsp; {trade.short_exchange?.toUpperCase()} SHORT
          &nbsp;·&nbsp; {formatDuration(trade.hold_minutes)}
        </div>

        {/* ── Prices ── */}
        <div style={{ marginBottom: 20 }}>
          <p style={sectionTitle}>{t.tradeDetailPrices}</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 20px' }}>
            <div>
              <div style={rowStyle}>
                <span style={labelStyle}>{t.entryPriceLong}</span>
                <span style={valueStyle}>{usd(trade.entry_price_long)}</span>
              </div>
              <div style={rowStyle}>
                <span style={labelStyle}>{t.exitPriceLong}</span>
                <span style={valueStyle}>{usd(trade.exit_price_long)}</span>
              </div>
            </div>
            <div>
              <div style={rowStyle}>
                <span style={labelStyle}>{t.entryPriceShort}</span>
                <span style={valueStyle}>{usd(trade.entry_price_short)}</span>
              </div>
              <div style={rowStyle}>
                <span style={labelStyle}>{t.exitPriceShort}</span>
                <span style={valueStyle}>{usd(trade.exit_price_short)}</span>
              </div>
            </div>
          </div>
        </div>

        {/* ── P&L Breakdown ── */}
        <div style={{ marginBottom: 20 }}>
          <p style={sectionTitle}>{t.tradeDetailPnl}</p>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.pricePnl}</span>
            <span style={{ ...valueStyle, color: pnlColor(pricePnl) }}>{usd(pricePnl)}</span>
          </div>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.fundingNetDetail}</span>
            <span style={{ ...valueStyle, color: pnlColor(fundingNet) }}>{usd(fundingNet)}</span>
          </div>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.feesDetail}</span>
            <span style={{ ...valueStyle, color: 'var(--red)' }}>{feesNum != null ? usd(feesNum) : '--'}</span>
          </div>
          <div style={totalRowStyle}>
            <span style={{ color: 'var(--text-primary)', fontWeight: 700, fontSize: 14 }}>{t.totalNetPnl}</span>
            <span style={{ fontSize: 16, fontWeight: 800, fontFamily: 'var(--font-mono, monospace)', color: pnlColor(totalPnl) }}>
              {usd(totalPnl)}
            </span>
          </div>
        </div>

        {/* ── Funding Collections ── */}
        <div style={{ marginBottom: 20 }}>
          <p style={sectionTitle}>{t.tradeDetailFunding}</p>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.collectionsCount}</span>
            <span style={valueStyle}>
              {trade.funding_collections != null ? trade.funding_collections : '--'}
            </span>
          </div>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.fundingCollectedUsd}</span>
            <span style={{ ...valueStyle, color: pnlColor(trade.funding_collected_usd) }}>
              {usd(trade.funding_collected_usd)}
            </span>
          </div>
          <div style={rowStyle}>
            <span style={labelStyle}>{t.entryEdge}</span>
            <span style={valueStyle}>{pct(trade.entry_spread)}</span>
          </div>
        </div>

        {/* ── Exit & Timing ── */}
        <div>
          <p style={sectionTitle}>{t.exitReasonLabel}</p>
          {trade.exit_reason ? (
            <div style={{
              background: 'var(--tag-bg, rgba(100,116,139,0.15))',
              borderRadius: 6, padding: '6px 12px', marginBottom: 12,
              fontSize: 13, fontFamily: 'var(--font-mono, monospace)',
              color: 'var(--text-secondary)', wordBreak: 'break-all',
            }}>
              {trade.exit_reason}
            </div>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 12 }}>--</div>
          )}

          <div style={rowStyle}>
            <span style={labelStyle}>{t.openedAt}</span>
            <span style={{ ...valueStyle, fontWeight: 400, color: 'var(--text-secondary)' }}>
              {formatDate(trade.opened_at ?? trade.open_time)}
            </span>
          </div>
          <div style={{ ...rowStyle, borderBottom: 'none' }}>
            <span style={labelStyle}>{t.closedAt}</span>
            <span style={{ ...valueStyle, fontWeight: 400, color: 'var(--text-secondary)' }}>
              {formatDate(trade.closed_at ?? trade.close_time)}
            </span>
          </div>
        </div>
      </div>
    </>
  );
};

export default TradeDetailModal;
