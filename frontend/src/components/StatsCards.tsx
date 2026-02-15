import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface StatsCardsProps {
  totalBalance: number;
  dailyPnl: number;
  activeTrades: number;
  systemRunning: boolean;
}

const StatsCards: React.FC<StatsCardsProps> = ({ totalBalance, dailyPnl, activeTrades, systemRunning }) => {
  const { t } = useSettings();

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <div className="stat-card stat-card--blue">
        <div className="stat-card-label">{t.totalBalance}</div>
        <div className="stat-card-value">{formatCurrency(totalBalance)}</div>
      </div>

      <div className="stat-card stat-card--green">
        <div className="stat-card-label">{t.dailyPnl}</div>
        <div className="stat-card-value" style={{ color: dailyPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
          {formatCurrency(dailyPnl)}
        </div>
      </div>

      <div className="stat-card stat-card--teal">
        <div className="stat-card-label">{t.activeTrades}</div>
        <div className="stat-card-value">{activeTrades}</div>
      </div>

      <div className="stat-card stat-card--purple">
        <div className="stat-card-label">{t.systemStatus}</div>
        <div className="stat-card-value" style={{ color: systemRunning ? 'var(--green)' : 'var(--red)', display: 'flex', alignItems: 'center', gap: 8 }}>
          {systemRunning ? t.running : t.stopped}
          <span className="status-dot" />
        </div>
      </div>
    </div>
  );
};

export default StatsCards;
