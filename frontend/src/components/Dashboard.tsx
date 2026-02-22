import React, { useState, useEffect, useRef, useCallback } from 'react';
import { FullData } from '../App';
import Sidebar from './Sidebar';
import Header from './Header';
import StatsCards from './StatsCards';
import PositionsTable from './PositionsTable';
import AnalyticsPanel from './AnalyticsPanel';
import ExchangeBalances from './ExchangeBalances';
import RecentTradesPanel from './RecentTradesPanel';
import RightPanel from './RightPanel';
import SystemLogs from './SystemLogs';

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
}

const Dashboard: React.FC<DashboardProps> = ({ data }) => {
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
        <Header botStatus={data.status} lastFetchedAt={data.lastFetchedAt} />

        <div className="content-area" ref={contentRef}>
          <div className="logo-watermark" style={{ backgroundImage: "url('/logo.png')" }} />
          <div className="space-y-5">
            <div id={SECTION_IDS.dashboard}>
              <StatsCards
                totalBalance={data.balances?.total ?? 0}
                dailyPnl={data.pnl?.total_pnl ?? 0}
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
              <RightPanel opportunities={data.opportunities} />
            </div>

            <AnalyticsPanel pnl={data.pnl} />

            <div id={SECTION_IDS.trades}>
              <RecentTradesPanel trades={data.trades || []} />
            </div>

            <div id={SECTION_IDS.logs}>
              <SystemLogs logs={data.logs} summary={data.summary} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
