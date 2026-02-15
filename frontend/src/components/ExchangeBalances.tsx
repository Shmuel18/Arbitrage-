import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface ExchangeBalancesProps {
  balances: { balances: Record<string, number>; total: number } | null;
}

const ExchangeBalances: React.FC<ExchangeBalancesProps> = ({ balances }) => {
  const { t } = useSettings();
  const entries = balances?.balances ? Object.entries(balances.balances) : [];
  const total = balances?.total ?? 0;
  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value);

  return (
    <div className="card p-5">
      <div className="card-header mb-4">{t.exchangePortfolio}</div>
      <div className="flex justify-between text-xs text-secondary mb-4 mono">
        <span>{t.total}</span>
        <span className="font-semibold text-accent">{formatCurrency(total)}</span>
      </div>
      {entries.length === 0 ? (
        <div className="text-muted text-sm">{t.noBalancesYet}</div>
      ) : (
        <div className="space-y-3 text-sm">
          {entries.map(([name, value]) => (
            <div key={name} className="flex justify-between items-center">
              <span className="text-secondary font-medium">{name.toUpperCase()}</span>
              <span className="mono font-semibold" style={{ color: 'var(--text-primary)' }}>{formatCurrency(value)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ExchangeBalances;
