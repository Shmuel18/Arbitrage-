import React from 'react';
import { FullData } from '../App';
import Header from './Header';
import StatsCards from './StatsCards';
import PositionsTable from './PositionsTable';
import ControlPanel from './ControlPanel';
import AnalyticsPanel from './AnalyticsPanel';
import ExchangeBalances from './ExchangeBalances';
import RightPanel from './RightPanel';
import SystemLogs from './SystemLogs';

interface DashboardProps {
  data: FullData;
}

const Dashboard: React.FC<DashboardProps> = ({ data }) => {
  return (
    <div className="flex flex-col min-h-screen text-white neon-bg relative">
      {/* Header */}
      <Header botStatus={data.status} />

      <div className="flex-1 p-3 md:p-4 space-y-4 relative z-10">
        <StatsCards
          totalBalance={data.balances?.total ?? 0}
          dailyPnl={data.pnl?.total_pnl ?? 0}
          activeTrades={data.status.active_positions}
          systemRunning={data.status.bot_running}
        />

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2">
            <PositionsTable positions={data.positions || []} />
          </div>
          <div className="space-y-4">
            <ControlPanel />
            <ExchangeBalances balances={data.balances} />
            <RightPanel opportunities={data.opportunities} />
          </div>
        </div>

        <AnalyticsPanel pnl={data.pnl} />

        <div className="border border-cyan-500/30 rounded-lg bg-slate-900/50 h-64 md:h-72">
          <SystemLogs logs={data.logs} summary={data.summary} />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
