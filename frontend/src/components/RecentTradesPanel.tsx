import React, { useState, memo } from 'react';
import { useSettings } from '../context/SettingsContext';
import { Trade } from '../types';
import TradeDetailModal from './TradeDetailModal';
import { TierBadge, ExitReasonBadge, formatCurrency, formatDate, formatDuration } from '../utils/format';

interface RecentTradesPanelProps {
  trades: Trade[];
  tradesLoaded?: boolean;
}

const RecentTradesPanel: React.FC<RecentTradesPanelProps> = ({ trades, tradesLoaded = true }) => {
  const { t } = useSettings();
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null);

  const formatPnl = (v?: number | null) => {
    if (v == null) return <span style={{ color: 'var(--text-muted)' }}>--</span>;
    const s = formatCurrency(v);
    return <span className={`nx-trades-pnl ${v >= 0 ? 'nx-trades-pnl--positive' : 'nx-trades-pnl--negative'}`}>{s}</span>;
  };

  const tierBadge = (tier?: string | null) => <TierBadge tier={tier} t={t} />;

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
        <div className="nx-section-header__icon" style={{ background: 'rgba(34,197,94,0.08)', borderColor: 'rgba(34,197,94,0.12)' }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>
          </svg>
        </div>
        {t.last10Trades}
        {trades.length > 0 && (
          <span className="nx-section-badge" style={{ marginLeft: 'auto' }}>
            {t.clickRowForDetails}
          </span>
        )}
      </div>
      <div className="overflow-auto scrollbar-thin">
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
              <th>{t.symbol}</th>
              <th>{t.longShort}</th>
              <th className="text-end">{t.netPnl}</th>
              <th className="text-end">{t.fundingNet}</th>
              <th className="text-end">{t.exitReasonLabel}</th>
              <th className="text-end">{t.duration}</th>
              <th className="text-end">{t.closed}</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center text-secondary py-8">
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
                  className="nx-trades-row"
                  onClick={() => setSelectedTrade(tr)}
                  style={{ animationDelay: `${trades.indexOf(tr) * 50}ms` }}
                  title="Click for trade details"
                >
                  <td style={{ overflow: 'hidden' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'nowrap', overflow: 'hidden' }}>
                      <span className="nx-trades-symbol" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tr.symbol.replace('/USDT:USDT', '').replace('/USDT', '')}
                      </span>
                      {tierBadge(tr.entry_tier)}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    <span className="nx-trades-exchange">
                      {tr.long_exchange?.toUpperCase()} → {tr.short_exchange?.toUpperCase()}
                    </span>
                  </td>
                  <td className="text-end mono">{formatPnl(tr.total_pnl)}</td>
                  <td className="text-end mono">
                    <span className={`nx-trades-funding ${fundingNet >= 0 ? 'nx-trades-pnl--positive' : 'nx-trades-pnl--negative'}`}>
                      {fundingNet >= 0 ? '+' : ''}{new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(fundingNet)}
                    </span>
                  </td>
                  <td className="text-end" style={{ whiteSpace: 'nowrap' }}>
                    <ExitReasonBadge reason={tr.exit_reason} />
                  </td>
                  <td className="text-end nx-trades-duration" style={{ whiteSpace: 'nowrap' }}>
                    {formatDuration(tr.hold_minutes)}
                  </td>
                  <td className="text-end nx-trades-date" style={{ whiteSpace: 'nowrap' }}>
                    {formatDate(tr.closed_at, true)}
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
