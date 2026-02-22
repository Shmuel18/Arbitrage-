import React, { useEffect, useRef } from 'react';
import { BotStatus } from '../types';
import { useSettings } from '../context/SettingsContext';

interface HeaderProps {
  botStatus: BotStatus;
  lastFetchedAt?: number;
}

const Header: React.FC<HeaderProps> = React.memo(({ botStatus, lastFetchedAt }) => {
  const { t, lang, setLang, theme, setTheme } = useSettings();
  const secsRef = useRef<HTMLElement>(null);
  const startRef = useRef(Date.now());

  // Reset start time when a new fetch arrives
  useEffect(() => { startRef.current = Date.now(); }, [lastFetchedAt]);

  // Update DOM directly every second — no React re-render
  useEffect(() => {
    const id = setInterval(() => {
      if (secsRef.current) {
        secsRef.current.textContent = Math.floor((Date.now() - startRef.current) / 1000) + 's';
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

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

        <div className="info-pill">
          {t.lastUpdated}: <strong ref={secsRef} style={{ display: 'inline-block', minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
        </div>
      </div>

      <div className="top-bar-right">
        <button onClick={toggleLang} className="topbar-btn" title={t.language}>
          {lang === 'en' ? 'עב' : 'EN'}
        </button>

        <button onClick={toggleTheme} className="topbar-btn" title={t.theme}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>
    </header>
  );
});

export default Header;
