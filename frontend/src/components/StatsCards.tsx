import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface StatsCardsProps {
  totalBalance: number;
  dailyPnl: number;
  activeTrades: number;
  systemRunning: boolean;
  winRate?: number;
  totalTrades?: number;
  allTimePnl?: number;
  avgPnl?: number;
}

const StatsCards: React.FC<StatsCardsProps> = ({
  totalBalance, dailyPnl, activeTrades, systemRunning,
  winRate = 0, totalTrades = 0, allTimePnl = 0, avgPnl = 0,
}) => {
  const { t } = useSettings();

  const fmt = (value: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value);

  const pnlColor = (v: number) => ({ color: v >= 0 ? 'var(--green)' : 'var(--red)' });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="stat-card stat-card--blue">
          <div className="stat-card-label">{t.totalBalance}</div>
          <div className="stat-card-value">{fmt(totalBalance)}</div>
        </div>
        <div className="stat-card stat-card--green">
          <div className="stat-card-label">{t.dailyPnl}</div>
          <div className="stat-card-value" style={pnlColor(dailyPnl)}>{fmt(dailyPnl)}</div>
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

      <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="stat-card stat-card--blue">
          <div className="stat-card-label">{t.allTimePnl}</div>
          <div className="stat-card-value" style={pnlColor(allTimePnl)}>{fmt(allTimePnl)}</div>
        </div>
        <div className="stat-card stat-card--green">
          <div className="stat-card-label">{t.winRate}</div>
          <div className="stat-card-value">{(winRate * 100).toFixed(1)}%</div>
        </div>
        <div className="stat-card stat-card--teal">
          <div className="stat-card-label">{t.avgPnlStat}</div>
          <div className="stat-card-value" style={pnlColor(avgPnl)}>{fmt(avgPnl)}</div>
        </div>
        <div className="stat-card stat-card--purple">
          <div className="stat-card-label">{t.totalTradesLabel}</div>
          <div className="stat-card-value">{totalTrades}</div>
        </div>
      </div>
    </div>
  );
};

export default StatsCards;
