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
    <div className="card p-5" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent)',
        borderRadius: '14px 14px 0 0',
      }} />

      <div className="card-header mb-4" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.8 }}>
            <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
          </svg>
          {t.exchangePortfolio}
        </div>
        <span className="mono" style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent)' }}>
          {formatCurrency(total)}
        </span>
      </div>

      {entries.length === 0 ? (
        <div className="text-muted text-sm">{t.noBalancesYet}</div>
      ) : (
        <div className="space-y-2">
          {entries.map(([name, value]) => {
            const pct = total > 0 ? (value / total) * 100 : 0;
            return (
              <div key={name}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.07em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{name}</span>
                  <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{formatCurrency(value)}</span>
                </div>
                {/* Mini progress bar */}
                <div style={{ height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${pct}%`,
                    background: 'linear-gradient(90deg, #06b6d4, #3b82f6)',
                    borderRadius: 4,
                    transition: 'width 0.6s ease',
                  }} />
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
