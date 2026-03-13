import React, { useState, useEffect, useRef, useCallback, useMemo, Suspense } from 'react';
import { m } from 'framer-motion';
import ViewReveal from './ViewReveal';
import { FullData } from '../hooks/useMarketData';
import { WsConnectionState } from '../services/websocket';
import { useSettings } from '../context/SettingsContext';
import Sidebar from './Sidebar';
import Header from './Header';
import StatsCards from './StatsCards';
import PositionsTable from './PositionsTable';
import ExchangeBalances from './ExchangeBalances';
import SignalTape from './SignalTape';
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
  const { t } = useSettings();

  // Stable array reference — `data.positions` changes only when positions list
  // actually mutates, preventing spurious re-renders of memo'd children.
  const positions = useMemo(() => data.positions ?? [], [data.positions]);
  const trades    = useMemo(() => data.trades    ?? [], [data.trades]);

  // Surface the most recent ERROR-level logs as a top-of-page banner.
  // Only show errors from the last 60 seconds to avoid stale banners.
  const errorLogs = useMemo(() => {
    const cutoff = Date.now() - 60_000;
    return (data.logs ?? [])
      .filter((l) => {
        if (l.level.toUpperCase() !== 'ERROR') return false;
        const ts = new Date(l.timestamp).getTime();
        // Drop entries with unparseable timestamps (legacy HH:MM:SS format)
        if (isNaN(ts)) return false;
        return ts > cutoff;
      })
      .slice(0, 5);
  }, [data.logs]);

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
      {/* Elite ambient glow orbs — fixed, behind all content */}
      <div className="elite-ambient" aria-hidden="true">
        <div className="elite-orb elite-orb--1" />
        <div className="elite-orb elite-orb--2" />
        <div className="elite-orb elite-orb--3" />
      </div>
      <Sidebar activeSection={activeSection} onNavigate={scrollToSection} />

      <div className="main-content">
        <Header
          botStatus={data.status}
          alerts={data.alerts ?? []}
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
            <ViewReveal
              id={SECTION_IDS.dashboard}
              className="elite-section"
              from="up"
              distance={28}
            >
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
                    {t.viewLogs}
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
            </ViewReveal>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 elite-section">
              <ViewReveal className="lg:col-span-2" id={SECTION_IDS.positions} from="left" distance={60}>
                <PositionsTable positions={positions} isLoading={isLoading} />
              </ViewReveal>
              <ViewReveal id={SECTION_IDS.balances} from="right" distance={60}>
                <ExchangeBalances balances={data.balances} />
              </ViewReveal>
            </div>

            <ViewReveal
              id={SECTION_IDS.opportunities}
              className="elite-section"
              from="up"
              distance={28}
            >
              <Suspense fallback={<SkeletonRightPanel rows={8} />}>
                <RightPanel opportunities={data.opportunities} status={data.status} />
              </Suspense>
            </ViewReveal>

            <ViewReveal className="elite-section" from="up" distance={28}>
            <Suspense fallback={<SkeletonAnalyticsPanel />}>
              <AnalyticsPanel
                pnl={data.pnl}
                pnlHours={pnlHours}
                onPnlHoursChange={onPnlHoursChange}
                totalBalance={data.balances?.total ?? 0}
              />
            </Suspense>
            </ViewReveal>

            <ViewReveal
              id={SECTION_IDS.trades}
              className="elite-section"
              from="up"
              distance={28}
            >
              <Suspense fallback={<SkeletonRecentTrades rows={6} />}>
                <RecentTradesPanel trades={trades} tradesLoaded={data.tradesLoaded} />
              </Suspense>
            </ViewReveal>

            <ViewReveal
              id={SECTION_IDS.logs}
              className="elite-section"
              from="up"
              distance={28}
            >
              <Suspense fallback={<div style={{ height: 280, borderRadius: 14 }} className="card skeleton-shimmer" />}>
                <SystemLogs logs={data.logs} summary={data.summary} />
              </Suspense>
            </ViewReveal>
          </div>
        </div>
      </div>
    </div>
  );
};

export default React.memo(Dashboard);
