import React, { Component, ErrorInfo, ReactNode } from 'react';
import Dashboard from './components/Dashboard';
import MemorialDayBanner from './components/MemorialDayBanner';
import { useMarketData } from './hooks/useMarketData';
import { useSettings } from './context/SettingsContext';
import { translations, Lang } from './i18n/translations';
import './App.css';
import './styles/memorial.css';

/**
 * ErrorBoundary can't use hooks, so read the language directly from
 * localStorage (set by SettingsProvider). Default to English if missing.
 */
function readLang(): Lang {
  try {
    const v = localStorage.getItem('trinity_lang');
    return v === 'en' ? 'en' : 'he';
  } catch {
    return 'he';
  }
}

/* ── Error Boundary ──────────────────────────────────────────────── */
interface ErrorBoundaryState { hasError: boolean; error?: Error }

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info);
  }
  render() {
    if (this.state.hasError) {
      const lang = readLang();
      const t = translations[lang];
      const isRtl = lang === 'he';
      return (
        <div className="nx-error-boundary" role="alert" aria-live="assertive" dir={isRtl ? 'rtl' : 'ltr'}>
          <div className="nx-error-boundary__card">
            <div className="nx-error-boundary__icon" aria-hidden="true">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <h2 className="nx-error-boundary__title">{t.ebTitle}</h2>
            <p className="nx-error-boundary__desc">{t.ebDesc}</p>
            {this.state.error?.message && (
              <details className="nx-error-boundary__details">
                <summary>{t.ebDetails}</summary>
                <pre>{this.state.error.message}</pre>
              </details>
            )}
            <div className="nx-error-boundary__actions">
              <button
                type="button"
                className="nx-error-boundary__btn nx-error-boundary__btn--primary"
                onClick={() => window.location.reload()}
              >
                {t.ebReload}
              </button>
              <button
                type="button"
                className="nx-error-boundary__btn"
                onClick={() => this.setState({ hasError: false, error: undefined })}
              >
                {t.ebRetry}
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function AppShell() {
  const { data, pnlHours, handlePnlHoursChange, wsConnection, lastWsMessageAt } = useMarketData();
  const { t } = useSettings();

  return (
    <div className="App min-h-screen bg-slate-900">
      {/* Skip-to-content link — first tabbable element for keyboard users */}
      <a href="#main-content" className="nx-skip-link">
        {t.ksSkipToContent}
      </a>
      {/* RateBridge status beam — stretches full width at very top */}
      <div className={`status-beam ${data.status.bot_running ? 'status-beam--running' : 'status-beam--stopped'}`} />
      <Dashboard
        data={data}
        pnlHours={pnlHours}
        onPnlHoursChange={handlePnlHoursChange}
        wsConnection={wsConnection}
        lastWsMessageAt={lastWsMessageAt}
      />
      {/* Yom HaZikaron memorial — floats above UI, dismissible */}
      <MemorialDayBanner />
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <AppShell />
    </ErrorBoundary>
  );
}

export default App;
