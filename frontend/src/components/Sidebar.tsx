import React from 'react';
import { useSettings } from '../context/SettingsContext';
import { SECTION_IDS, SectionId } from './Dashboard';

/* ── SVG nav icons — consistent 18×18, stroke-based ──────────── */
const IconDashboard = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="4" rx="1.5" />
    <rect x="14" y="11" width="7" height="10" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" />
  </svg>
);
const IconPositions = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
);
const IconOpportunities = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    <line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" />
  </svg>
);
const IconTrades = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" />
  </svg>
);
const IconPortfolio = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
    <line x1="12" y1="12" x2="12" y2="16" /><line x1="10" y1="14" x2="14" y2="14" />
  </svg>
);
const IconLogs = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
  </svg>
);

interface SidebarProps {
  activeSection: SectionId;
  onNavigate: (sectionId: SectionId) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeSection, onNavigate }) => {
  const { t } = useSettings();

  const navItems: { id: SectionId; icon: React.ReactNode; label: string }[] = [
    { id: SECTION_IDS.dashboard,     icon: <IconDashboard />,     label: t.dashboard },
    { id: SECTION_IDS.positions,     icon: <IconPositions />,     label: t.activePositions },
    { id: SECTION_IDS.opportunities, icon: <IconOpportunities />, label: t.liveOpportunities },
    { id: SECTION_IDS.trades,        icon: <IconTrades />,        label: t.last10Trades },
    { id: SECTION_IDS.balances,      icon: <IconPortfolio />,     label: t.exchangePortfolio },
    { id: SECTION_IDS.logs,          icon: <IconLogs />,          label: t.systemLogs },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-logo nx-sidebar-logo">
        <img src="/logo.png" alt="RateBridge" className="sidebar-logo-img" />
        <h1 className="nx-sidebar-title">RATEBRIDGE</h1>
        <span>{t.arbitrageEngine}</span>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`sidebar-nav-item nx-nav-item${activeSection === item.id ? ' active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer nx-sidebar-footer">
        <div className="nx-sidebar-version">v2.1</div>
      </div>
    </aside>
  );
};

export default Sidebar;
