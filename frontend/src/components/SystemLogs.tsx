import React, { useRef, useEffect } from 'react';
import { useSettings } from '../context/SettingsContext';

interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

interface SystemLogsProps {
  logs: LogEntry[];
  summary: { total_trades: number; win_rate: number } | null;
}

const SystemLogs: React.FC<SystemLogsProps> = ({ logs, summary }) => {
  const { t } = useSettings();
  const containerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);

  // Track whether user is near the bottom (within 60px)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Only auto-scroll when user is near the bottom (don't hijack their scroll position)
  useEffect(() => {
    if (containerRef.current && isNearBottomRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  const getLevelClass = (level: string) => {
    switch (level.toUpperCase()) {
      case 'INFO': return 'nx-log-level--info';
      case 'SUCCESS': return 'nx-log-level--success';
      case 'WARNING': return 'nx-log-level--warning';
      case 'ERROR': return 'nx-log-level--error';
      default: return '';
    }
  };

  const getLevelColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'INFO': return '#2DB8C4';
      case 'SUCCESS': return 'var(--green)';
      case 'WARNING': return 'var(--yellow)';
      case 'ERROR': return 'var(--red)';
      default: return 'var(--text-secondary)';
    }
  };

  const tradeCount = summary?.total_trades ?? 0;
  const winRate = summary?.win_rate ? (summary.win_rate * 100).toFixed(1) : '0.0';

  return (
    <div className="logs-container" style={{ height: '280px', position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(45,184,196,0.35), transparent)',
        borderRadius: '14px 14px 0 0',
        zIndex: 1,
      }} />

      <div className="flex justify-between items-center px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', gap: 12 }}>
        <div className="nx-section-header">
          <div className="nx-section-header__icon" style={{ background: 'rgba(45,184,196,0.08)', borderColor: 'rgba(45,184,196,0.12)' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#2DB8C4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          {t.systemLogs}
        </div>
        <div className="nx-log-stats">
          {t.totalTradesLabel}: <span className="nx-log-stat-value" style={{ color: '#2DB8C4' }}>{tradeCount}</span>
          <span style={{ opacity: 0.3 }}>|</span>
          {t.winRate}: <span className="nx-log-stat-value" style={{ color: parseFloat(winRate) >= 50 ? 'var(--green)' : 'var(--red)' }}>{winRate}%</span>
        </div>
      </div>

      <div
        ref={containerRef}
        className="flex-1 overflow-auto px-5 py-3 scrollbar-thin"
        tabIndex={0}
        role="log"
        aria-live="polite"
        aria-label="System logs"
      >
        {logs.length === 0 ? (
          <div className="text-muted text-sm">{t.waitingLogs}</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="nx-log-entry mb-1">
              <span className="nx-log-timestamp">[{log.timestamp}]</span>
              <span className={`nx-log-level ${getLevelClass(log.level)}`}>{log.level}</span>
              <span className="nx-log-message" style={{ color: getLevelColor(log.level) }}>{log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
