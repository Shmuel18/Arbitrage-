import React from 'react';
import { BotStatus } from '../types';
import { emergencyStop } from '../services/api';
import { useSettings } from '../context/SettingsContext';

interface HeaderProps {
  botStatus: BotStatus;
}

const Header: React.FC<HeaderProps> = ({ botStatus }) => {
  const { t, lang, setLang, theme, setTheme } = useSettings();

  const handleEmergencyStop = async () => {
    if (window.confirm(t.emergencyStopConfirm)) {
      try {
        await emergencyStop();
        alert(t.emergencyStopSent);
      } catch (error) {
        console.error('Error:', error);
        alert(t.emergencyStopFailed);
      }
    }
  };

  const toggleLang = () => setLang(lang === 'en' ? 'he' : 'en');
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');

  return (
    <header className="panel panel-strong border-b border-cyan-500/20">
      <div className="px-4 md:px-6 py-4 relative z-10">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <div className="text-3xl font-bold text-cyan-300 tracking-wide">
              {t.trinityTitle}
              <span className="text-sm text-cyan-500 ml-2 mono">{t.arbitrageEngine}</span>
            </div>
            <div className={`px-3 py-1 rounded-full text-xs mono border ${
              botStatus.bot_running
                ? 'bg-green-500/10 text-green-300 border-green-500/40'
                : 'bg-red-500/10 text-red-300 border-red-500/40'
            }`}>
              {botStatus.bot_running ? t.running : t.stopped}
              <span className="status-dot" />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs mono">
            <div className="text-gray-400">
              {t.exchanges}:
              <span className="text-cyan-200 ml-2">
                {botStatus.connected_exchanges.length > 0
                  ? botStatus.connected_exchanges.join(', ')
                  : t.none}
              </span>
            </div>
            <div className="text-gray-400">
              {t.positions}:
              <span className="text-cyan-200 ml-2">{botStatus.active_positions}</span>
            </div>

            {/* Language toggle */}
            <button
              onClick={toggleLang}
              className="px-3 py-2 bg-cyan-500/10 text-cyan-200 border border-cyan-500/30 rounded mono"
              title={t.language}
            >
              {lang === 'en' ? 'עב' : 'EN'}
            </button>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="px-3 py-2 bg-purple-500/10 text-purple-200 border border-purple-500/30 rounded mono"
              title={t.theme}
            >
              {theme === 'dark' ? '☀️' : '🌙'}
            </button>

            <button
              onClick={handleEmergencyStop}
              className="px-3 py-2 bg-red-600/30 text-red-200 border border-red-500/40 rounded mono"
            >
              {t.emergencyStop}
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
