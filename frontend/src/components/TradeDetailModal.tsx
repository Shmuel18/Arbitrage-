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
    const p = Math.abs(n) <= 1 ? n * 100 : n;
    return `${p >= 0 ? '+' : ''}${p.toFixed(4)}%`;
  };

  const formatDate = (value?: string | null) => {
    if (!value) return '--';
    try {
      return new Intl.DateTimeFormat('default', {
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      }).format(new Date(value));
    } catch { return '--'; }
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
    let label = m.replace('_', ' ').toUpperCase();
    let color = '#22d3ee'; // default Cyan
    let emoji = '';
    
    if (m === 'cherry_pick') { color = '#f97316'; emoji = '🍒 '; label = t.cherry_pick; }
    if (m === 'pot') { color = '#f59e0b'; emoji = '🍯 '; label = t.pot; }
    if (m === 'nutcracker') { color = '#a855f7'; emoji = '🔨🥜 '; label = t.nutcracker; }
    if (m === 'hold' || m === 'hold_mixed') { color = '#22c55e'; emoji = '🤝 '; label = t.hold; }

    return (
      <span style={{
        background: color + '22', color, border: `1px solid ${color}55`,
        borderRadius: 4, padding: '1px 8px', fontSize: 11, fontWeight: 700,
        textTransform: 'uppercase', letterSpacing: '0.06em',
      }}>
        {emoji}{label}
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

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          zIndex: 1000, backdropFilter: 'blur(4px)',
        }}
      />

      <div style={{
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
              {modeBadge(trade.mode)}
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
              <span style={valueStyle}>{usd(trade.entry_price_long, 5)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingAtEntry}</span>
              <span style={valueStyle}>{pct(trade.long_funding_rate)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.exitPriceLong}</span>
              <span style={valueStyle}>{usd(trade.exit_price_long, 5)}</span>
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
              <span style={valueStyle}>{usd(trade.entry_price_short, 5)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingAtEntry}</span>
              <span style={valueStyle}>{pct(trade.short_funding_rate)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.exitPriceShort}</span>
              <span style={valueStyle}>{usd(trade.exit_price_short, 5)}</span>
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
              <span style={{ ...valueStyle, color: pnlColor(pricePnl) }}>{usd(pricePnl)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.fundingNetDetail}</span>
              <span style={{ ...valueStyle, color: pnlColor(fundingNet) }}>{usd(fundingNet)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.feesDetail}</span>
              <span style={{ ...valueStyle, color: 'var(--red)' }}>{usd(feesNum)}</span>
            </div>
            <div style={totalRowStyle}>
              <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>{t.totalNetPnl}</span>
              <span style={{ fontSize: 18, fontWeight: 900, color: pnlColor(totalPnl) }}>{usd(totalPnl)}</span>
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
              <span style={{ ...valueStyle, color: 'var(--green)' }}>{usd(trade.funding_collected_usd)}</span>
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t.entryEdge}</span>
              <span style={valueStyle}>{pct(trade.entry_spread)}</span>
            </div>
          </div>
        </div>

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

export default TradeDetailModal;
