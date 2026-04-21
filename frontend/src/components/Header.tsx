import React, { useEffect, useRef, useState } from 'react';
import { Alert, BotStatus } from '../types';
import { useSettings } from '../context/SettingsContext';
import { WsConnectionState } from '../services/websocket';
import AlertBell from './AlertBell';

interface HeaderProps {
  botStatus: BotStatus;
  alerts?: Alert[];
  lastFetchedAt?: number;
  wsConnection: WsConnectionState;
  lastWsMessageAt?: number | null;
  onMobileMenuToggle?: () => void;
}

/* ── Icons — stroke-based, consistent with Sidebar ─────────────── */
const IconMenu = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);
const IconSun = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
  </svg>
);
const IconMoon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  </svg>
);
const IconChevron = ({ open }: { open: boolean }) => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
  >
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

const Header: React.FC<HeaderProps> = React.memo(({
  botStatus,
  alerts = [],
  lastFetchedAt,
  wsConnection,
  lastWsMessageAt,
  onMobileMenuToggle,
}) => {
  const { t, lang, setLang, theme, setTheme } = useSettings();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const secsRef = useRef<HTMLElement>(null);
  const wsSecsRef = useRef<HTMLElement>(null);
  const wsAgePillRef = useRef<HTMLDivElement>(null);
  const stalePillRef = useRef<HTMLDivElement>(null);
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

  const toggleLang = () => setLang(lang === 'en' ? 'he' : 'en');
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');

  return (
    <header className="top-bar">
      <div className="top-bar-left">
        {/* Mobile hamburger — hidden on desktop via CSS */}
        <button
          type="button"
          className="nx-topbar-btn nx-mobile-menu-btn"
          onClick={onMobileMenuToggle}
          aria-label={t.hMenuOpen}
        >
          <IconMenu />
        </button>

        {/* ── Primary status: always visible ────────── */}
        <div className={`status-badge ${botStatus.bot_running ? 'status-badge--running' : 'status-badge--stopped'}`}>
          <span className="status-dot" />
          {botStatus.bot_running ? t.running : t.stopped}
        </div>

        <div className="nx-info-pill">
          {t.positions}: <strong>{botStatus.active_positions}</strong>
        </div>

        <div className={wsPillClass} title="WebSocket transport health">
          <span className="nx-health-pill__dot" />WS {wsConnection}
        </div>

        {/* ── Stale-data warning (only shown when relevant) ── */}
        <div ref={stalePillRef} className="nx-health-pill nx-health-pill--down" style={{ display: 'none' }} title="Data may be stale">
          STALE DATA
        </div>

        {/* ── Secondary details: collapsible ────────── */}
        <button
          type="button"
          className={`nx-details-toggle${detailsOpen ? ' nx-details-toggle--open' : ''}`}
          onClick={() => setDetailsOpen((o) => !o)}
          aria-expanded={detailsOpen}
          aria-label={t.hDetailsToggleLabel}
        >
          <span>{t.hDetails}</span>
          <IconChevron open={detailsOpen} />
        </button>

        {detailsOpen && (
          <div className="nx-details-group">
            <div className="nx-info-pill">
              {t.exchanges}: <strong>
                {botStatus.connected_exchanges.length > 0
                  ? botStatus.connected_exchanges.join(', ')
                  : t.none}
              </strong>
            </div>

            <div className="nx-info-pill">
              {t.lastUpdated}: <strong ref={secsRef} style={{ display: 'inline-block', minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
            </div>

            <div ref={wsAgePillRef} className="nx-health-pill nx-health-pill--ok" title="Time since last websocket message">
              WS age: <strong ref={wsSecsRef} style={{ minWidth: '3.5ch', textAlign: 'right' }}>0s</strong>
            </div>
          </div>
        )}
      </div>

      <div className="top-bar-right">
        <AlertBell alerts={alerts} />

        <button
          onClick={toggleLang}
          className="nx-topbar-btn"
          aria-label={t.language}
          title={t.language}
        >
          {lang === 'en' ? 'עב' : 'EN'}
        </button>

        <button
          onClick={toggleTheme}
          className="nx-topbar-btn"
          aria-label={t.theme}
          title={t.theme}
        >
          {theme === 'dark' ? <IconSun /> : <IconMoon />}
        </button>
      </div>
    </header>
  );
});

export default Header;
