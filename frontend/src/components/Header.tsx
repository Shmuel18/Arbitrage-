import React from 'react';
import { BotStatus } from '../types';
import { sendBotCommand, emergencyStop } from '../services/api';

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
    <header className="bg-slate-800 border-b border-slate-700 shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <h1 className="text-3xl font-bold gradient-bg bg-clip-text text-transparent">
              Trinity Bot
            </h1>
            <div className={`px-3 py-1 rounded-full text-sm font-semibold ${
              botStatus.bot_running 
                ? 'bg-green-500/20 text-green-400 border border-green-500/50' 
                : 'bg-red-500/20 text-red-400 border border-red-500/50'
            }`}>
              {botStatus.bot_running ? '🟢 Running' : '🔴 Stopped'}
            </div>
          </div>

          <div className="flex items-center space-x-4">
            <div className="text-sm">
              <span className="text-slate-400">Exchanges:</span>
              <span className="ml-2 font-semibold text-white">
                {botStatus.connected_exchanges.length > 0 
                  ? botStatus.connected_exchanges.join(', ') 
                  : 'None'}
              </span>
            </div>

            <div className="text-sm">
              <span className="text-slate-400">Positions:</span>
              <span className="ml-2 font-semibold text-white">
                {botStatus.active_positions}
              </span>
            </div>

            <button
              onClick={handleEmergencyStop}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-bold rounded-lg transition-colors"
            >
              🚨 EMERGENCY STOP
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
