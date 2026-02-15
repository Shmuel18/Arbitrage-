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
    <div className="logs-container" style={{ height: '280px' }}>
      <div className="flex justify-between items-center px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)' }}>
        <div className="card-header">{t.systemLogs}</div>
        <div className="text-xs text-secondary">{t.totalTradesLabel}: {tradeCount} | {t.winRate}: {winRate}%</div>
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
