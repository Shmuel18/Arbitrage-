import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface RecentTrade {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_qty: string;
  short_qty: string;
  entry_price_long?: string | null;
  entry_price_short?: string | null;
  exit_price_long?: string | null;
  exit_price_short?: string | null;
  fees_paid_total?: string | null;
  funding_received_total?: string | null;
  funding_paid_total?: string | null;
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
  status?: string | null;
}

interface RecentTradesPanelProps {
  trades: RecentTrade[];
}

const RecentTradesPanel: React.FC<RecentTradesPanelProps> = ({ trades }) => {
  const { t } = useSettings();

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

  return (
    <div className="panel panel-strong">
      <div className="panel-header text-xs px-4 py-3 border-b border-cyan-500/20">
        {t.last10Trades}
      </div>
      <div className="overflow-auto scrollbar-thin">
        <table className="neon-table w-full text-xs mono">
          <thead className="sticky top-0 bg-slate-900/80">
            <tr className="border-b border-cyan-500/10 text-gray-500">
              <th className="text-left py-2 px-3">{t.symbol}</th>
              <th className="text-left py-2 px-3">{t.longShort}</th>
              <th className="text-right py-2 px-3">{t.entryLS}</th>
              <th className="text-right py-2 px-3">{t.exitLS}</th>
              <th className="text-right py-2 px-3">{t.fundingLS}</th>
              <th className="text-right py-2 px-3">{t.fundingNet}</th>
              <th className="text-right py-2 px-3">{t.fees}</th>
              <th className="text-right py-2 px-3">{t.opened}</th>
              <th className="text-right py-2 px-3">{t.closed}</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-gray-500 py-6">{t.noTradesYet}</td>
              </tr>
            ) : (
              trades.map((tr) => (
                <tr key={tr.id} className="border-b border-slate-800/40 hover:bg-slate-800/30">
                  <td className="py-2 px-3 text-cyan-300">{tr.symbol}</td>
                  <td className="py-2 px-3 text-gray-300">
                    {tr.long_exchange?.toUpperCase()} / {tr.short_exchange?.toUpperCase()}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatCurrency(tr.entry_price_long)} / {formatCurrency(tr.entry_price_short)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatCurrency(tr.exit_price_long)} / {formatCurrency(tr.exit_price_short)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatRate(tr.long_funding_rate)} / {formatRate(tr.short_funding_rate)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatFunding(tr.funding_received_total)} / {formatFunding(tr.funding_paid_total)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-300">
                    {formatFunding(tr.fees_paid_total)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-400">
                    {formatDate(tr.opened_at)}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-400">
                    {formatDate(tr.closed_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-gray-500 px-4 py-2">
        {t.fundingEstimated}
      </div>
    </div>
  );
};

export default RecentTradesPanel;
