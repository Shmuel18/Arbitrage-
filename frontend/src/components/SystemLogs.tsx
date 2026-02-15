import React, { useRef, useEffect } from 'react';

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
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  const getLevelColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'INFO': return 'text-cyan-400';
      case 'SUCCESS': return 'text-green-400';
      case 'WARNING': return 'text-yellow-400';
      case 'ERROR': return 'text-red-400';
      default: return 'text-gray-400';
    }
  };

  const tradeCount = summary?.total_trades ?? 0;
  const winRate = summary?.win_rate ? (summary.win_rate * 100).toFixed(1) : '0.0';

  return (
    <div className="flex flex-col h-full panel panel-strong">
      <div className="panel-header text-xs px-4 py-2 border-b border-cyan-500/30 flex justify-between">
        <div>System Logs (Live)</div>
        <div className="text-gray-500">Total Trades: {tradeCount} | Win Rate: {winRate}%</div>
      </div>

      <div ref={containerRef} className="flex-1 overflow-auto px-4 py-2 mono text-xs scrollbar-thin">
        {logs.length === 0 ? (
          <div className="text-gray-600">Waiting for logs...</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="mb-1">
              <span className="text-gray-600">[{log.timestamp}]</span>
              <span className={` ${getLevelColor(log.level)}`}> {log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
