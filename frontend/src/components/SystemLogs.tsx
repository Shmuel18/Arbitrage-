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

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  const getLevelClass = (level: string) => {
    switch (level.toUpperCase()) {
      case 'INFO': return 'log-info';
      case 'SUCCESS': return 'log-success';
      case 'WARNING': return 'log-warning';
      case 'ERROR': return 'log-error';
      default: return '';
    }
  };

  const tradeCount = summary?.total_trades ?? 0;
  const winRate = summary?.win_rate ? (summary.win_rate * 100).toFixed(1) : '0.0';

  return (
    <div className="logs-container" style={{ height: '280px', position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(59,130,246,0.35), transparent)',
        borderRadius: '14px 14px 0 0',
        zIndex: 1,
      }} />

      <div className="flex justify-between items-center px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', gap: 12 }}>
        <div className="card-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
          </svg>
          {t.systemLogs}
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.05em' }}>
          {t.totalTradesLabel}: <span style={{ color: '#60a5fa' }}>{tradeCount}</span>
          <span style={{ margin: '0 8px', opacity: 0.3 }}>|</span>
          {t.winRate}: <span style={{ color: winRate >= '50.0' ? 'var(--green)' : 'var(--red)' }}>{winRate}%</span>
        </div>
      </div>

      <div ref={containerRef} className="flex-1 overflow-auto px-5 py-3 scrollbar-thin">
        {logs.length === 0 ? (
          <div className="text-muted text-sm">{t.waitingLogs}</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="log-entry mb-1">
              <span className="log-timestamp">[{log.timestamp}]</span>
              <span className={getLevelClass(log.level)}> {log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
