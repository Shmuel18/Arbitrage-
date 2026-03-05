import React from 'react';
import { useSettings } from '../context/SettingsContext';
import { formatCurrency } from '../utils/format';

interface ExchangeBalancesProps {
  balances: { balances: Record<string, number>; total: number } | null;
}

const ExchangeBalances: React.FC<ExchangeBalancesProps> = ({ balances }) => {
  const { t } = useSettings();
  const entries = balances?.balances ? Object.entries(balances.balances) : [];
  const total = balances?.total ?? 0;

  return (
    <div className="card p-5" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent)',
        borderRadius: '14px 14px 0 0',
      }} />

      <div className="card-header mb-4" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="nx-section-header">
          <div className="nx-section-header__icon" style={{ background: 'rgba(6,182,212,0.08)', borderColor: 'rgba(6,182,212,0.12)' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
            </svg>
          </div>
          {t.exchangePortfolio}
        </div>
        <span className="nx-exch-total">
          {formatCurrency(total)}
        </span>
      </div>

      {entries.length === 0 ? (
        <div className="text-muted text-sm">{t.noBalancesYet}</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {entries.map(([name, value]) => {
            const pct = total > 0 ? (value / total) * 100 : 0;
            return (
              <div key={name} className="nx-exch-item">
                <div className="nx-exch-header">
                  <span className="nx-exch-name">{name}</span>
                  <div>
                    <span className="nx-exch-value">{formatCurrency(value)}</span>
                    <span className="nx-exch-pct">{pct.toFixed(1)}%</span>
                  </div>
                </div>
                <div className="nx-exch-bar">
                  <div className="nx-exch-bar__fill" style={{ width: `${pct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ExchangeBalances;
