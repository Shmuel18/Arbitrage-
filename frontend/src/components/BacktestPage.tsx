import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useSettings } from '../context/SettingsContext';
import BacktestReportsPage from './BacktestReportsPage';
import BacktestFetchForm from './BacktestFetchForm';
import BacktestRunForm from './BacktestRunForm';

type TabId = 'reports' | 'fetch' | 'run';

const BacktestPage: React.FC = () => {
  const { t } = useSettings();
  const [activeTab, setActiveTab] = useState<TabId>('reports');

  // Stable translations — read via lookup so we don't need new i18n keys for
  // every tab label (short English labels are fine for tabs).
  const tabs = useMemo<{ id: TabId; label: string }[]>(() => [
    { id: 'reports', label: t.backtestReports },
    { id: 'fetch', label: t.backtestFetchTab },
    { id: 'run', label: t.backtestRunTab },
  ], [t]);

  return (
    <div className="bt-app">
      <header className="bt-app-header">
        <Link to="/" className="bt-back-link">← {t.backToDashboard}</Link>
        <h1 className="bt-app-title">{t.backtests}</h1>
        <div className="bt-app-spacer" />
      </header>

      <nav className="bt-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`bt-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="bt-app-main">
        {activeTab === 'reports' && <BacktestReportsPage />}
        {activeTab === 'fetch' && <BacktestFetchForm />}
        {activeTab === 'run' && <BacktestRunForm />}
      </main>
    </div>
  );
};

export default BacktestPage;
