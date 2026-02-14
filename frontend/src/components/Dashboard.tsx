import React from 'react';
import { FullData } from '../App';
import Header from './Header';
import LeftPanel from './LeftPanel';
import CenterPanel from './CenterPanel';
import RightPanel from './RightPanel';
import SystemLogs from './SystemLogs';

interface DashboardProps {
  data: FullData;
}

const Dashboard: React.FC<DashboardProps> = ({ data }) => {
  return (
    <div className="flex flex-col h-screen bg-slate-950 text-white">
      {/* Header */}
      <Header botStatus={data.status} />

      {/* Main Content - 3 Column Layout */}
      <div className="flex flex-1 overflow-hidden gap-2 p-2">
        {/* Left Panel - Portfolio Stats */}
        <div className="w-1/4 overflow-auto">
          <LeftPanel summary={data.summary} balances={data.balances} />
        </div>

        {/* Center Panel - Network Diagram */}
        <div className="w-2/4 overflow-auto">
          <CenterPanel
            exchanges={data.status.connected_exchanges}
            balances={data.balances}
            summary={data.summary}
          />
        </div>

        {/* Right Panel - Opportunities */}
        <div className="w-1/4 overflow-auto">
          <RightPanel opportunities={data.opportunities} />
        </div>
      </div>

      {/* System Logs - Bottom */}
      <div className="h-1/5 border-t border-cyan-500/30 bg-slate-900/50">
        <SystemLogs logs={data.logs} summary={data.summary} />
      </div>
    </div>
  );
};

export default Dashboard;
