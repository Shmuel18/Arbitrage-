import React, { useState } from 'react';
import { useSettings } from '../context/SettingsContext';
import { Trade } from '../types';
import TradeDetailModal from './TradeDetailModal';

interface RecentTradesPanelProps {
  trades: Trade[];
}

const RecentTradesPanel: React.FC<RecentTradesPanelProps> = ({ trades }) => {
  const { t } = useSettings();
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null);

  const formatCurrency = (value?: string | null) => {
    if (!value) return '--';
    const n = Number(value);
    if (Number.isNaN(n)) return '--';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(n);
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
      return new Date(value).toLocaleString();
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

  return (
  <>
    <div className="card">
      <div className="card-header px-5 py-4 border-b" style={{ borderColor: 'var(--card-border)' }}>
        {t.last10Trades}
      </div>
      <div className="overflow-auto scrollbar-thin">
        <table className="corp-table">
          <thead>
            <tr>
              <th>{t.symbol}</th>
              <th>{t.longShort}</th>
              <th className="text-end">{t.entryLS}</th>
              <th className="text-end">{t.exitLS}</th>
              <th className="text-end">{t.fundingLS}</th>
              <th className="text-end">{t.fundingNet}</th>
              <th className="text-end">{t.fees}</th>
              <th className="text-end">{t.netPnl}</th>
              <th className="text-end">{t.duration}</th>
              <th className="text-end">{t.opened}</th>
              <th className="text-end">{t.closed}</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={11} className="text-center text-secondary py-8">{t.noTradesYet}</td>
              </tr>
            ) : (
              trades.map((tr) => (
                <tr
                  key={tr.id}
                  onClick={() => setSelectedTrade(tr)}
                  style={{ cursor: 'pointer' }}
                  title="Click for trade details"
                >
                  <td className="font-semibold text-accent">{tr.symbol}</td>
                  <td>
                    {tr.long_exchange?.toUpperCase()} / {tr.short_exchange?.toUpperCase()}
                  </td>
                  <td className="text-end mono">
                    {formatCurrency(tr.entry_price_long)} / {formatCurrency(tr.entry_price_short)}
                  </td>
                  <td className="text-end mono">
                    {formatCurrency(tr.exit_price_long)} / {formatCurrency(tr.exit_price_short)}
                  </td>
                  <td className="text-end mono">
                    {formatRate(tr.long_funding_rate)} / {formatRate(tr.short_funding_rate)}
                  </td>
                  <td className="text-end mono">
                    {formatFunding(tr.funding_received_total)} / {formatFunding(tr.funding_paid_total)}
                  </td>
                  <td className="text-end mono">
                    {formatFunding(tr.fees_paid_total)}
                  </td>
                  <td className="text-end mono">
                    {formatPnl(tr.total_pnl)}
                  </td>
                  <td className="text-end text-secondary">
                    {formatDuration(tr.hold_minutes)}
                  </td>
                  <td className="text-end text-secondary">
                    {formatDate(tr.opened_at)}
                  </td>
                  <td className="text-end text-secondary">
                    {formatDate(tr.closed_at)}
                  </td>
                </tr>
              ))
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

export default RecentTradesPanel;
