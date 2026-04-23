import React, { useState, useEffect, useRef, useCallback, Suspense, lazy } from 'react';
import { FullData } from '../hooks/useMarketData';
import { WsConnectionState } from '../services/websocket';
import Sidebar from './Sidebar';
import Header from './Header';
import StatsCards from './StatsCards';
import PositionsTable from './PositionsTable';
import ExchangeBalances from './ExchangeBalances';
import RightPanel from './RightPanel';
import SignalTape from './SignalTape';
import KeyboardShortcuts from './KeyboardShortcuts';
import { useSettings } from '../context/SettingsContext';
import { useToast } from '../context/ToastContext';
import { useTelegram } from '../context/TelegramContext';

// Below-the-fold components — lazy-loaded to shrink initial bundle
const AnalyticsPanel    = lazy(() => import('./AnalyticsPanel'));
const RecentTradesPanel = lazy(() => import('./RecentTradesPanel'));
const SystemLogs        = lazy(() => import('./SystemLogs'));

/** Fallback placeholder while a chunk is loading — preserves layout */
const LazyFallback: React.FC<{ minHeight?: number }> = ({ minHeight = 200 }) => (
  <div
    className="nx-lazy-fallback"
    style={{ minHeight }}
    role="status"
    aria-label="Loading section"
  >
    <div className="nx-lazy-fallback__shimmer" />
  </div>
);

export const SECTION_IDS = {
  dashboard: 'section-dashboard',
  positions: 'section-positions',
  opportunities: 'section-opportunities',
  trades: 'section-trades',
  balances: 'section-balances',
  logs: 'section-logs',
} as const;

export type SectionId = typeof SECTION_IDS[keyof typeof SECTION_IDS];

interface DashboardProps {
  data: FullData;
  pnlHours: number;
  onPnlHoursChange: (hours: number) => void;
  wsConnection: WsConnectionState;
  lastWsMessageAt: number | null;
}

const Dashboard: React.FC<DashboardProps> = ({
  data,
  pnlHours,
  onPnlHoursChange,
  wsConnection,
  lastWsMessageAt,
}) => {
  const [activeSection, setActiveSection] = useState<SectionId>(SECTION_IDS.dashboard);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const { theme, setTheme } = useSettings();
  const toast = useToast();
  const toggleTheme = useCallback(
    () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    [theme, setTheme]
  );

  // WS connection lifecycle → user-visible notifications
  const prevWsRef = useRef<WsConnectionState>(wsConnection);
  useEffect(() => {
    const prev = prevWsRef.current;
    prevWsRef.current = wsConnection;
    // Only announce state transitions, not the initial mount value
    if (prev === wsConnection) return;
    if (prev === 'connected' && wsConnection !== 'connected') {
      toast.push({
        intent: 'warning',
        title: 'Connection lost',
        message: 'Live data stream disconnected. Attempting to reconnect…',
        duration: 6000,
      });
    } else if (prev !== 'connected' && wsConnection === 'connected') {
      toast.push({
        intent: 'success',
        title: 'Reconnected',
        message: 'Live data stream restored.',
        duration: 3000,
      });
    }
  }, [wsConnection, toast]);

  const scrollToSection = useCallback((sectionId: SectionId) => {
    const el = document.getElementById(sectionId);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    setActiveSection(sectionId);
  }, []);

  // Scroll-spy: track which section is currently visible
  useEffect(() => {
    const sectionIds = Object.values(SECTION_IDS);
    const observer = new IntersectionObserver(
      (entries) => {
        // Find the most visible section
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible.length > 0) {
          setActiveSection(visible[0].target.id as SectionId);
        }
      },
      {
        root: contentRef.current,
        rootMargin: '-10% 0px -60% 0px',
        threshold: [0, 0.25, 0.5, 0.75, 1],
      }
    );

    sectionIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, []);

  // Mini-App context — drop keyboard-only UX inside Telegram (no hardware kbd).
  const { isTelegramWebApp } = useTelegram();

  return (
    <div className={`app-layout${isTelegramWebApp ? ' app-layout--telegram' : ''}`}>
      {!isTelegramWebApp && (
        <KeyboardShortcuts onNavigate={scrollToSection} onToggleTheme={toggleTheme} />
      )}

      <Sidebar
        activeSection={activeSection}
        onNavigate={scrollToSection}
        mobileOpen={mobileMenuOpen}
        onMobileClose={() => setMobileMenuOpen(false)}
      />

      <div className="main-content">
        <Header
          botStatus={data.status}
          alerts={(data as any).alerts ?? []}
          lastFetchedAt={data.lastFetchedAt}
          wsConnection={wsConnection}
          lastWsMessageAt={lastWsMessageAt}
          onMobileMenuToggle={() => setMobileMenuOpen((o) => !o)}
        />
        <SignalTape logs={data.logs} />

        <main className="content-area" ref={contentRef} id="main-content" aria-label="Dashboard main content">
          <div className="logo-watermark" style={{ backgroundImage: "url('/logo.png')" }} />
          <div className="space-y-5">
            <div id={SECTION_IDS.dashboard}>
              <StatsCards
                totalBalance={data.balances?.total ?? 0}
                dailyPnl={data.dailyPnl}
                activeTrades={data.status.active_positions}
                systemRunning={data.status.bot_running}
                winRate={data.summary?.win_rate ?? 0}
                totalTrades={data.summary?.total_trades ?? 0}
                allTimePnl={data.summary?.all_time_pnl ?? 0}
                avgPnl={data.summary?.avg_pnl ?? 0}
              />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              <div className="lg:col-span-2" id={SECTION_IDS.positions}>
                <PositionsTable positions={data.positions || []} />
              </div>
              <div id={SECTION_IDS.balances}>
                <ExchangeBalances balances={data.balances} />
              </div>
            </div>

            <div id={SECTION_IDS.opportunities}>
              <RightPanel opportunities={data.opportunities} status={data.status} />
            </div>

            <Suspense fallback={<LazyFallback minHeight={260} />}>
              <AnalyticsPanel
                pnl={data.pnl}
                pnlHours={pnlHours}
                onPnlHoursChange={onPnlHoursChange}
                totalBalance={data.balances?.total ?? 0}
              />
            </Suspense>

            <div id={SECTION_IDS.trades}>
              <Suspense fallback={<LazyFallback minHeight={320} />}>
                <RecentTradesPanel trades={data.trades || []} tradesLoaded={data.tradesLoaded} />
              </Suspense>
            </div>

            <div id={SECTION_IDS.logs}>
              <Suspense fallback={<LazyFallback minHeight={240} />}>
                <SystemLogs logs={data.logs} summary={data.summary} />
              </Suspense>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
};

export default React.memo(Dashboard);
