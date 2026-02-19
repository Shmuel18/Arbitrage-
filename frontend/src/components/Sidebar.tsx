import React from 'react';
import { useSettings } from '../context/SettingsContext';
import { SECTION_IDS, SectionId } from './Dashboard';

interface SidebarProps {
  activeSection: SectionId;
  onNavigate: (sectionId: SectionId) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeSection, onNavigate }) => {
  const { t } = useSettings();

  const navItems: { id: SectionId; icon: string; label: string }[] = [
    { id: SECTION_IDS.dashboard,     icon: '📊', label: t.dashboard },
    { id: SECTION_IDS.positions,     icon: '💹', label: t.activePositions },
    { id: SECTION_IDS.opportunities, icon: '🔍', label: t.liveOpportunities },
    { id: SECTION_IDS.trades,        icon: '📈', label: t.last10Trades },
    { id: SECTION_IDS.balances,      icon: '💰', label: t.exchangePortfolio },
    { id: SECTION_IDS.logs,          icon: '📋', label: t.systemLogs },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <img src="/logo.png" alt="Trinity" className="sidebar-logo-img" />
        <h1>TRINITY</h1>
        <span>{t.arbitrageEngine}</span>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`sidebar-nav-item${activeSection === item.id ? ' active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <button
          className={`sidebar-nav-item${activeSection === SECTION_IDS.control ? ' active' : ''}`}
          onClick={() => onNavigate(SECTION_IDS.control)}
        >
          <span className="nav-icon">⚙️</span>
          <span>{t.controlPanel}</span>
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
