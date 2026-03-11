import React, { useState, useEffect, useRef, useCallback, useMemo, Suspense } from 'react';
import { FullData } from '../hooks/useMarketData';
import { WsConnectionState } from '../services/websocket';
import Sidebar from './Sidebar';
import Header from './Header';
import StatsCards from './StatsCards';
import PositionsTable from './PositionsTable';
import ExchangeBalances from './ExchangeBalances';
import SignalTape from './SignalTape';
import RiskRadar from './RiskRadar';
import {
  SkeletonRightPanel,
  SkeletonAnalyticsPanel,
  SkeletonRecentTrades,
} from './Skeleton';

// Below-the-fold sections are lazily loaded — reduces initial JS parse time.
const RightPanel    = React.lazy(() => import('./RightPanel'));
const AnalyticsPanel = React.lazy(() => import('./AnalyticsPanel'));
const RecentTradesPanel = React.lazy(() => import('./RecentTradesPanel'));
const SystemLogs    = React.lazy(() => import('./SystemLogs'));

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
  wsAttempts: number;
}

const Dashboard: React.FC<DashboardProps> = ({
  data,
  pnlHours,
  onPnlHoursChange,
  wsConnection,
  lastWsMessageAt,
  wsAttempts,
}) => {
  // True only before the very first data payload arrives — both stay null
  // until WS sends the initial full_update. Once set, never goes back to null.
  const isLoading = data.balances === null && data.summary === null;

  // Stable array reference — `data.positions` changes only when positions list
  // actually mutates, preventing spurious re-renders of memo'd children.
  const positions = useMemo(() => data.positions ?? [], [data.positions]);
  const trades    = useMemo(() => data.trades    ?? [], [data.trades]);

  // Surface the most recent ERROR-level logs as a top-of-page banner.
  const errorLogs = useMemo(
    () => (data.logs ?? []).filter((l) => l.level.toUpperCase() === 'ERROR').slice(0, 5),
    [data.logs],
  );

  const [activeSection, setActiveSection] = useState<SectionId>(SECTION_IDS.dashboard);
  const contentRef = useRef<HTMLDivElement>(null);

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

  return (
    <div className="app-layout">
      <Sidebar activeSection={activeSection} onNavigate={scrollToSection} />

      <div className="main-content">
        <Header
          botStatus={data.status}
          lastFetchedAt={data.lastFetchedAt}
          wsConnection={wsConnection}
          lastWsMessageAt={lastWsMessageAt}
          wsAttempts={wsAttempts}
        />
        <SignalTape
          logs={data.logs}
          onSignalClick={(key) => scrollToSection(
            (SECTION_IDS as Record<string, SectionId>)[key] ?? SECTION_IDS.dashboard
          )}
        />

        <div className="content-area" ref={contentRef}>
          <div className="logo-watermark" style={{ backgroundImage: "url('/logo.png')" }} />
          <div className="space-y-5">
            <div id={SECTION_IDS.dashboard}>
              {errorLogs.length > 0 && (
                <div className="nx-error-banner">
                  <span className="nx-error-banner__icon">🚨</span>
                  <span className="nx-error-banner__msg">{errorLogs[0].message}</span>
                  {errorLogs.length > 1 && (
                    <span className="nx-error-banner__count">+{errorLogs.length - 1}</span>
                  )}
                  <button
                    className="nx-error-banner__btn"
                    onClick={() => scrollToSection(SECTION_IDS.logs)}
                  >
                    View Logs ↓
                  </button>
                </div>
              )}
              <StatsCards
                totalBalance={data.balances?.total ?? 0}
                dailyPnl={data.dailyPnl}
                activeTrades={data.status.active_positions}
                systemRunning={data.status.bot_running}
                winRate={data.summary?.win_rate ?? 0}
                totalTrades={data.summary?.total_trades ?? 0}
                allTimePnl={data.summary?.all_time_pnl ?? 0}
                avgPnl={data.summary?.avg_pnl ?? 0}
                isLoading={isLoading}
              />
            </div>

            <RiskRadar
              positions={positions}
              totalBalance={data.balances?.total ?? 0}
              dailyPnl={data.dailyPnl}
              allTimePnl={data.summary?.all_time_pnl ?? 0}
            />

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              <div className="lg:col-span-2" id={SECTION_IDS.positions}>
                <PositionsTable positions={positions} isLoading={isLoading} />
              </div>
              <div id={SECTION_IDS.balances}>
                <ExchangeBalances balances={data.balances} />
              </div>
            </div>

            <div id={SECTION_IDS.opportunities}>
              <Suspense fallback={<SkeletonRightPanel rows={8} />}>
                <RightPanel opportunities={data.opportunities} status={data.status} />
              </Suspense>
            </div>

            <Suspense fallback={<SkeletonAnalyticsPanel />}>
              <AnalyticsPanel
                pnl={data.pnl}
                pnlHours={pnlHours}
                onPnlHoursChange={onPnlHoursChange}
                totalBalance={data.balances?.total ?? 0}
              />
            </Suspense>

            <div id={SECTION_IDS.trades}>
              <Suspense fallback={<SkeletonRecentTrades rows={6} />}>
                <RecentTradesPanel trades={trades} tradesLoaded={data.tradesLoaded} />
              </Suspense>
            </div>

            <div id={SECTION_IDS.logs}>
              <Suspense fallback={<div style={{ height: 280, borderRadius: 14 }} className="card skeleton-shimmer" />}>
                <SystemLogs logs={data.logs} summary={data.summary} />
              </Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default React.memo(Dashboard);
