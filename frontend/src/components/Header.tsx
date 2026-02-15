import React from 'react';
import { BotStatus } from '../types';
import { emergencyStop } from '../services/api';

interface HeaderProps {
  botStatus: BotStatus;
}

const Header: React.FC<HeaderProps> = ({ botStatus }) => {
  const handleEmergencyStop = async () => {
    if (window.confirm('⚠️ Are you sure you want to EMERGENCY STOP? This will close all positions!')) {
      try {
        await emergencyStop();
        alert('Emergency stop initiated!');
      } catch (error) {
        console.error('Error:', error);
        alert('Failed to send emergency stop command');
      }
    }
  };

  return (
    <header className="panel panel-strong border-b border-cyan-500/20">
      <div className="px-4 md:px-6 py-4 relative z-10">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <div className="text-3xl font-bold text-cyan-300 tracking-wide">
              TRINITY
              <span className="text-sm text-cyan-500 ml-2 mono">ARBITRAGE ENGINE</span>
            </div>
            <div className={`px-3 py-1 rounded-full text-xs mono border ${
              botStatus.bot_running
                ? 'bg-green-500/10 text-green-300 border-green-500/40'
                : 'bg-red-500/10 text-red-300 border-red-500/40'
            }`}>
              {botStatus.bot_running ? 'RUNNING' : 'STOPPED'}
              <span className="status-dot" />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs mono">
            <div className="text-gray-400">
              EXCHANGES:
              <span className="text-cyan-200 ml-2">
                {botStatus.connected_exchanges.length > 0
                  ? botStatus.connected_exchanges.join(', ')
                  : 'NONE'}
              </span>
            </div>
            <div className="text-gray-400">
              POSITIONS:
              <span className="text-cyan-200 ml-2">{botStatus.active_positions}</span>
            </div>
            <button
              onClick={handleEmergencyStop}
              className="px-3 py-2 bg-red-600/30 text-red-200 border border-red-500/40 rounded mono"
            >
              EMERGENCY STOP
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
