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
    <header className="top-bar">
      <div className="top-bar-left">
        <div className={`status-badge ${botStatus.bot_running ? 'status-badge--running' : 'status-badge--stopped'}`}>
          <span className="status-dot" />
          {botStatus.bot_running ? t.running : t.stopped}
        </div>

        <div className="info-pill">
          {t.exchanges}: <strong>
            {botStatus.connected_exchanges.length > 0
              ? botStatus.connected_exchanges.join(', ')
              : t.none}
          </strong>
        </div>

        <div className="info-pill">
          {t.positions}: <strong>{botStatus.active_positions}</strong>
        </div>
      </div>

      <div className="top-bar-right">
        <button onClick={toggleLang} className="topbar-btn" title={t.language}>
          {lang === 'en' ? 'עב' : 'EN'}
        </button>

        <button onClick={toggleTheme} className="topbar-btn" title={t.theme}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>

        <button onClick={handleEmergencyStop} className="topbar-btn topbar-btn--danger">
          {t.emergencyStop}
        </button>
      </div>
    </header>
  );
};

export default Header;
