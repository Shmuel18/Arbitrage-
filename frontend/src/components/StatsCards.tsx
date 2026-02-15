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
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
      <div className="neon-card p-4">
        <div className="text-cyan-300 text-xs mono">{t.totalBalance}</div>
        <div className="text-2xl font-bold text-white mt-2">{formatCurrency(totalBalance)}</div>
      </div>

      <div className="neon-card neon-card--green p-4">
        <div className="text-cyan-300 text-xs mono">{t.dailyPnl}</div>
        <div className={`text-2xl font-bold mt-2 ${dailyPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {formatCurrency(dailyPnl)}
        </div>
      </div>

      <div className="neon-card p-4">
        <div className="text-cyan-300 text-xs mono">{t.activeTrades}</div>
        <div className="text-2xl font-bold text-white mt-2">{activeTrades}</div>
      </div>

      <div className="neon-card neon-card--purple p-4">
        <div className="text-cyan-300 text-xs mono">{t.systemStatus}</div>
        <div className={`text-2xl font-bold mt-2 flex items-center ${systemRunning ? 'text-green-400' : 'text-red-400'}`}>
          {systemRunning ? t.running : t.stopped}
          <span className="status-dot" />
        </div>
      </div>
    </div>
  );
};

export default StatsCards;
