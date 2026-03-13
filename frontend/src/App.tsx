import React, { Component, ErrorInfo, ReactNode } from 'react';
import { LazyMotion, domAnimation, m } from 'framer-motion';
import Dashboard from './components/Dashboard';
import { useMarketData } from './hooks/useMarketData';
import './App.css';

import { SettingsContext } from './context/SettingsContext';
import { translations, Lang } from './i18n/translations';

/* ── Error Fallback (functional — enables motion + CSS vars) ─────── */
function ErrorFallback({ error, onReload }: { error?: Error; onReload: () => void }) {
  const ctx = React.useContext(SettingsContext);
  const lang: Lang = ctx?.lang ?? 'en';
  const t = translations[lang];
  return (
    <m.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        padding: 'var(--space-6)',
      }}
    >
      <div
        className="card xcard"
        style={{
          maxWidth: 480,
          width: '100%',
          textAlign: 'center',
          padding: 'var(--space-8)',
        }}
      >
        {/* Icon */}
        <m.div
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.15, type: 'spring', stiffness: 280, damping: 22 }}
          style={{
            width: 56,
            height: 56,
            borderRadius: 'var(--radius-full)',
            background: 'rgba(239,68,68,0.12)',
            border: '1.5px solid rgba(239,68,68,0.35)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto var(--space-5)',
            fontSize: 26,
          }}
        >
          ⚠
        </m.div>

        <h2 style={{
          fontSize: 'var(--text-lg)',
          fontWeight: 'var(--fw-semibold)',
          color: 'var(--text)',
          marginBottom: 'var(--space-3)',
        }}>
          {t.errorBoundaryTitle}
        </h2>

        <p style={{
          fontSize: 'var(--text-sm)',
          color: 'var(--muted)',
          marginBottom: 'var(--space-4)',
        }}>
          {t.errorBoundaryMessage}
        </p>

        {error?.message && (
          <pre style={{
            fontSize: 'var(--text-2xs)',
            color: 'var(--muted)',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            padding: 'var(--space-3)',
            marginBottom: 'var(--space-6)',
            textAlign: 'left',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {error.message}
          </pre>
        )}

        <button
          onClick={onReload}
          style={{
            padding: 'var(--space-2) var(--space-6)',
            borderRadius: 'var(--radius-md)',
            background: 'var(--accent)',
            color: '#fff',
            border: 'none',
            cursor: 'pointer',
            fontSize: 'var(--text-sm)',
            fontWeight: 'var(--fw-medium)',
            letterSpacing: '0.02em',
          }}
        >
          {t.reloadPage}
        </button>
      </div>
    </m.div>
  );
}

/* ── Error Boundary (class — required for getDerivedStateFromError) ─ */
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
      return (
        <ErrorFallback
          error={this.state.error}
          onReload={() => window.location.reload()}
        />
      );
    }
    return this.props.children;
  }
}

function App() {
  const { data, pnlHours, handlePnlHoursChange, wsConnection, lastWsMessageAt, wsAttempts } = useMarketData();
  const settingsCtx = React.useContext(SettingsContext);
  const tApp = settingsCtx?.t ?? translations.en;

  return (
    <LazyMotion features={domAnimation}>
      <ErrorBoundary>
        <div className="App min-h-screen bg-slate-900">
          {/* RateBridge status beam — stretches full width at very top */}
          <div className={`status-beam ${data.status.bot_running ? 'status-beam--running' : 'status-beam--stopped'}`} />
          {/* API connectivity error — shown when all REST poll requests fail */}
          {data.fetchError && (
            <div
              role="alert"
              aria-live="polite"
              style={{
                background: 'rgba(239,68,68,0.1)',
                borderBottom: '1px solid rgba(239,68,68,0.3)',
                color: '#f87171',
                padding: '7px 16px',
                fontSize: '13px',
                textAlign: 'center',
                letterSpacing: '0.01em',
              }}
            >
              ⚠ {data.fetchError} — {tApp.displayingLastKnownData}
            </div>
          )}
          <Dashboard
            data={data}
            pnlHours={pnlHours}
            onPnlHoursChange={handlePnlHoursChange}
            wsConnection={wsConnection}
            lastWsMessageAt={lastWsMessageAt}
            wsAttempts={wsAttempts}
          />
        </div>
      </ErrorBoundary>
    </LazyMotion>
  );
}

export default App;
