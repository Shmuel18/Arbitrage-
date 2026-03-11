import React, { useEffect, useRef, useCallback } from 'react';
import { BotStatus } from '../types';
import { useSettings } from '../context/SettingsContext';
import { WsConnectionState } from '../services/websocket';

interface HeaderProps {
  botStatus: BotStatus;
  lastFetchedAt?: number;
  wsConnection: WsConnectionState;
  lastWsMessageAt?: number | null;
  /** Current reconnect attempt (1-based). 0 = not reconnecting. */
  wsAttempts?: number;
}

const Header: React.FC<HeaderProps> = React.memo(({
  botStatus,
  lastFetchedAt,
  wsConnection,
  lastWsMessageAt,
  wsAttempts = 0,
}) => {
  const { t, lang, setLang, theme, setTheme } = useSettings();
  const secsRef = useRef<HTMLElement>(null);
  const wsSecsRef = useRef<HTMLElement>(null);
  const wsAgePillRef = useRef<HTMLDivElement>(null);
  const stalePillRef = useRef<HTMLDivElement>(null);
  const heartbeatRef = useRef<SVGSVGElement>(null);
  const startRef = useRef(Date.now());
  const wsStartRef = useRef<number>(Date.now());

  // Reset start time when a new fetch arrives
  useEffect(() => { startRef.current = Date.now(); }, [lastFetchedAt]);
  useEffect(() => {
    wsStartRef.current = lastWsMessageAt ?? Date.now();
  }, [lastWsMessageAt]);

  useEffect(() => {
    if (!wsAgePillRef.current) return;
    wsAgePillRef.current.className = 'nx-health-pill nx-health-pill--ok';
  }, [lastWsMessageAt]);

  // Heartbeat pulse: direct DOM class toggle on every WS message — zero React re-render.
  useEffect(() => {
    const el = heartbeatRef.current;
    if (!el) return;
    el.classList.remove('hb-pulse');
    void el.getBoundingClientRect(); // force reflow so animation restarts
    el.classList.add('hb-pulse');
  }, [lastWsMessageAt]);

  // Update DOM directly every second — no React re-render
  useEffect(() => {
    const id = setInterval(() => {
      if (secsRef.current) {
        secsRef.current.textContent = Math.floor((Date.now() - startRef.current) / 1000) + 's';
      }
      if (wsSecsRef.current) {
        const wsAgeSec = Math.floor((Date.now() - wsStartRef.current) / 1000);
        wsSecsRef.current.textContent = wsAgeSec + 's';
        if (wsAgePillRef.current) {
          wsAgePillRef.current.className =
            wsAgeSec <= 10
              ? 'nx-health-pill nx-health-pill--ok'
              : wsAgeSec <= 20
              ? 'nx-health-pill nx-health-pill--warn'
              : 'nx-health-pill nx-health-pill--down';
        }
        if (stalePillRef.current) {
          stalePillRef.current.style.display = wsAgeSec > 20 ? 'inline-flex' : 'none';
        }
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

  // Stable toggle callbacks — functional updater form avoids capturing
  // stale lang/theme values while keeping the dep array minimal.
  const toggleLang  = useCallback(() => setLang(lang   === 'en'   ? 'he'    : 'en'),    [lang,  setLang]);
  const toggleTheme = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [theme, setTheme]);

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
          <span className="nx-health-pill__dot" />
          WS {wsConnection}
          {wsConnection === 'reconnecting' && wsAttempts > 0 && (
            <span style={{ opacity: 0.7, marginInlineStart: 4 }}>
              ({wsAttempts}/20)
            </span>
          )}
        </div>

        <div ref={wsAgePillRef} className="nx-health-pill nx-health-pill--ok" title="Time since last websocket message">
          WS age: <strong ref={wsSecsRef} style={{ minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
        </div>

        {/* Heartbeat: pulses on every WS message via direct DOM class toggle */}
        <svg
          ref={heartbeatRef}
          className="nx-heartbeat"
          width="28" height="16"
          viewBox="0 0 28 16"
          fill="none"
          aria-hidden
        >
          <polyline
            points="0,8 4,8 6,2 8,14 10,4 12,12 14,8 28,8"
            stroke="#10b981"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>

        <div ref={stalePillRef} className="nx-health-pill nx-health-pill--down" style={{ display: 'none' }} title="Data may be stale">
          STALE DATA
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
