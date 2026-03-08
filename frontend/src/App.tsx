import React, { Component, ErrorInfo, ReactNode } from 'react';
import Dashboard from './components/Dashboard';
import { useMarketData } from './hooks/useMarketData';
import './App.css';

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
      return (
        <div style={{ padding: 40, textAlign: 'center', color: '#ef4444' }}>
          <h2>Something went wrong</h2>
          <pre style={{ fontSize: 12, color: '#94a3b8', marginTop: 12 }}>
            {this.state.error?.message}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{ marginTop: 20, padding: '8px 20px', borderRadius: 8, background: '#3b82f6', color: '#fff', border: 'none', cursor: 'pointer' }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function App() {
  const { data, pnlHours, handlePnlHoursChange, wsConnection, lastWsMessageAt } = useMarketData();

  return (
    <ErrorBoundary>
      <div className="App min-h-screen bg-slate-900">
        {/* RateBridge status beam — stretches full width at very top */}
        <div className={`status-beam ${data.status.bot_running ? 'status-beam--running' : 'status-beam--stopped'}`} />
        <Dashboard
          data={data}
          pnlHours={pnlHours}
          onPnlHoursChange={handlePnlHoursChange}
          wsConnection={wsConnection}
          lastWsMessageAt={lastWsMessageAt}
        />
      </div>
    </ErrorBoundary>
  );
}

export default App;
