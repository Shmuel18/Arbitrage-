import React, { useEffect, useRef } from 'react';
import { BotStatus } from '../types';
import { useSettings } from '../context/SettingsContext';
import { WsConnectionState } from '../services/websocket';

interface HeaderProps {
  botStatus: BotStatus;
  lastFetchedAt?: number;
  wsConnection: WsConnectionState;
  lastWsMessageAt?: number | null;
}

const Header: React.FC<HeaderProps> = React.memo(({
  botStatus,
  lastFetchedAt,
  wsConnection,
  lastWsMessageAt,
}) => {
  const { t, lang, setLang, theme, setTheme } = useSettings();
  const secsRef = useRef<HTMLElement>(null);
  const wsSecsRef = useRef<HTMLElement>(null);
  const startRef = useRef(Date.now());
  const wsStartRef = useRef<number>(Date.now());

  // Reset start time when a new fetch arrives
  useEffect(() => { startRef.current = Date.now(); }, [lastFetchedAt]);
  useEffect(() => {
    wsStartRef.current = lastWsMessageAt ?? Date.now();
  }, [lastWsMessageAt]);

  // Update DOM directly every second — no React re-render
  useEffect(() => {
    const id = setInterval(() => {
      if (secsRef.current) {
        secsRef.current.textContent = Math.floor((Date.now() - startRef.current) / 1000) + 's';
      }
      if (wsSecsRef.current) {
        wsSecsRef.current.textContent = Math.floor((Date.now() - wsStartRef.current) / 1000) + 's';
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const wsPillClass =
    wsConnection === 'connected'
      ? 'nx-health-pill nx-health-pill--ok'
      : wsConnection === 'reconnecting'
      ? 'nx-health-pill nx-health-pill--warn'
      : 'nx-health-pill nx-health-pill--down';

  const stalenessSec = Math.floor((Date.now() - (lastFetchedAt ?? Date.now())) / 1000);
  const stalenessClass =
    stalenessSec <= 10
      ? 'nx-health-pill nx-health-pill--ok'
      : stalenessSec <= 20
      ? 'nx-health-pill nx-health-pill--warn'
      : 'nx-health-pill nx-health-pill--down';

  const toggleLang = () => setLang(lang === 'en' ? 'he' : 'en');
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');

  return (
    <header className="top-bar">
      <div className="top-bar-left">
        <div className={`status-badge ${botStatus.bot_running ? 'status-badge--running' : 'status-badge--stopped'}`}>
          <span className="status-dot" />
          {botStatus.bot_running ? t.running : t.stopped}
        </div>

        <div className="nx-info-pill">
          {t.exchanges}: <strong>
            {botStatus.connected_exchanges.length > 0
              ? botStatus.connected_exchanges.join(', ')
              : t.none}
          </strong>
        </div>

        <div className="nx-info-pill">
          {t.positions}: <strong>{botStatus.active_positions}</strong>
        </div>

        <div className="nx-info-pill">
          {t.lastUpdated}: <strong ref={secsRef} style={{ display: 'inline-block', minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
        </div>

        <div className={wsPillClass} title="WebSocket transport health">
          <span className="nx-health-pill__dot" />WS {wsConnection}
        </div>

        <div className={stalenessClass} title="Time since last websocket message">
          WS age: <strong ref={wsSecsRef} style={{ minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
        </div>
      </div>

      <div className="top-bar-right">
        <button onClick={toggleLang} className="nx-topbar-btn" title={t.language}>
          {lang === 'en' ? 'עב' : 'EN'}
        </button>

        <button onClick={toggleTheme} className="nx-topbar-btn" title={t.theme}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>
    </header>
  );
});

export default Header;
