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
    <div className="panel panel-strong p-4">
      <div className="panel-header text-xs mb-3">{t.exchangePortfolio}</div>
      <div className="flex justify-between text-xs text-gray-400 mb-3 mono">
        <span>{t.total}</span>
        <span className="text-cyan-300 font-mono">{formatCurrency(total)}</span>
      </div>
      {entries.length === 0 ? (
        <div className="text-gray-500 text-sm">{t.noBalancesYet}</div>
      ) : (
        <div className="space-y-2 text-sm mono">
          {entries.map(([name, value]) => (
            <div key={name} className="flex justify-between">
              <span className="text-gray-300">{name.toUpperCase()}</span>
              <span className="text-cyan-400 font-mono">{formatCurrency(value)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ExchangeBalances;
