import React, { useState, memo } from 'react';
import { useSettings } from '../context/SettingsContext';
import { Trade } from '../types';
import TradeDetailModal from './TradeDetailModal';

interface RecentTradesPanelProps {
  trades: Trade[];
  tradesLoaded?: boolean;
}

const RecentTradesPanel: React.FC<RecentTradesPanelProps> = ({ trades, tradesLoaded = true }) => {
  const { t } = useSettings();
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null);

  const formatPrice = (value?: string | null) => {
    if (!value) return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(n);
  };

  const formatFunding = (value?: string | null) => {
    if (!value) return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4 }).format(n);
  };

  const formatRate = (value?: string | null) => {
    if (!value) return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    const pct = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const formatDate = (value?: string | null) => {
    if (!value) return '--';
    try {
      return new Intl.DateTimeFormat('default', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      }).format(new Date(value));
    } catch {
      return '--';
    }
  };

  const formatPnl = (v?: number | null) => {
    if (v == null) return '--';
    const s = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(v);
    return <span style={{ color: v >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>{s}</span>;
  };

  const formatDuration = (mins?: number | null) => {
    if (mins == null) return '--';
    if (mins < 60) return `${Math.round(mins)}m`;
    return `${Math.floor(mins / 60)}h${Math.round(mins % 60) > 0 ? Math.round(mins % 60) + 'm' : ''}`;
  };

  const tierBadge = (tier?: string | null) => {
    if (!tier) return null;
    const key = tier.toLowerCase();
    let label = tier.toUpperCase();
    let color = '#94a3b8';
    let emoji = '';

    if (key === 'top')     { color = '#f59e0b'; emoji = '🏆 '; label = t.tierTop; }
    if (key === 'medium')  { color = '#3b82f6'; emoji = '📊 '; label = t.tierMedium; }
    if (key === 'bad')     { color = '#ef4444'; emoji = '⚠️ '; label = t.tierBad; }
    if (key === 'adverse') { color = '#6b7280'; emoji = ''; label = t.tierAdverse; }

    return (
      <span style={{
        background: color + '18', color, border: `1px solid ${color}44`,
        borderRadius: 4, padding: '0px 6px', fontSize: 10, fontWeight: 700,
        letterSpacing: '0.06em', marginLeft: 4,
      }}>
        {emoji}{label}
      </span>
    );
  };

  return (
  <>
    <div className="card" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(34,197,94,0.45), transparent)',
        borderRadius: '14px 14px 0 0',
        zIndex: 1, pointerEvents: 'none',
      }} />
      <div className="card-header px-5 py-4 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.8 }}>
          <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>
        </svg>
        {t.last10Trades}
        {trades.length > 0 && (
          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
            {t.clickRowForDetails}
          </span>
        )}
      </div>
      <div className="overflow-auto scrollbar-thin">
        <table className="corp-table" style={{ tableLayout: 'fixed', width: '100%' }}>
          <colgroup>
            <col style={{ width: '22%' }} />
            <col style={{ width: '18%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '9%' }} />
            <col style={{ width: '23%' }} />
          </colgroup>
          <thead>
            <tr>
              <th>{t.symbol}</th>
              <th>{t.longShort}</th>
              <th className="text-end">{t.netPnl}</th>
              <th className="text-end">{t.fundingNet}</th>
              <th className="text-end">{t.duration}</th>
              <th className="text-end">{t.closed}</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-secondary py-8">
                    {tradesLoaded ? t.noTradesYet : '...'}
                  </td>
              </tr>
            ) : (
              trades.map((tr) => {
                const received = Number(tr.funding_received_total ?? 0);
                const paid = Number(tr.funding_paid_total ?? 0);
                const fundingNet = received - paid;
                return (
                <tr
                  key={tr.id}
                  onClick={() => setSelectedTrade(tr)}
                  style={{ cursor: 'pointer' }}
                  title="Click for trade details"
                >
                  <td style={{ overflow: 'hidden' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'nowrap', overflow: 'hidden' }}>
                      <span className="font-semibold text-accent" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tr.symbol.replace('/USDT:USDT', '').replace('/USDT', '')}
                      </span>
                      {tierBadge(tr.entry_tier)}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    <span style={{ fontSize: 11 }}>
                      {tr.long_exchange?.toUpperCase()} → {tr.short_exchange?.toUpperCase()}
                    </span>
                  </td>
                  <td className="text-end mono">{formatPnl(tr.total_pnl)}</td>
                  <td className="text-end mono">
                    <span style={{ color: fundingNet >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                      {fundingNet >= 0 ? '+' : ''}{new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(fundingNet)}
                    </span>
                  </td>
                  <td className="text-end text-secondary" style={{ whiteSpace: 'nowrap' }}>
                    {formatDuration(tr.hold_minutes)}
                  </td>
                  <td className="text-end text-secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                    {formatDate(tr.closed_at)}
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-muted px-5 py-3">
        {t.fundingEstimated}
      </div>
    </div>

    {selectedTrade && (
      <TradeDetailModal
        trade={selectedTrade}
        onClose={() => setSelectedTrade(null)}
      />
    )}
  </>
  );
};

export default memo(RecentTradesPanel);
