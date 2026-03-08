import React, { memo } from 'react';

interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

interface SignalTapeProps {
  logs: LogEntry[];
}

const LEVEL_COLOR: Record<string, string> = {
  ERROR:   '#ef4444',
  WARNING: '#f59e0b',
  INFO:    '#06b6d4',
  DEBUG:   '#475569',
};

const levelColor = (level: string): string =>
  LEVEL_COLOR[level.toUpperCase()] ?? '#475569';

const SignalTape: React.FC<SignalTapeProps> = memo(({ logs }) => {
  if (!logs || logs.length === 0) return null;

  // Take the 30 most recent entries; filter out noise.
  const items = logs.slice(0, 30);

  // Duplicate items so the marquee loops seamlessly:
  // The inner container is 200% wide, second half mirrors the first.
  const renderItems = (keyPrefix: string) =>
    items.map((log, i) => {
      const color = levelColor(log.level);
      return (
        <span key={`${keyPrefix}-${i}`} className="signal-tape__item" style={{ color }}>
          <span className="signal-tape__dot" style={{ background: color }} />
          {log.message}
          <span className="signal-tape__sep">·</span>
        </span>
      );
    });

  return (
    <div className="signal-tape" aria-hidden="true">
      <div className="signal-tape__label">FEED</div>
      <div className="signal-tape__track">
        <div className="signal-tape__inner">
          {renderItems('a')}
          {renderItems('b')}
        </div>
      </div>
    </div>
  );
});

SignalTape.displayName = 'SignalTape';

export default SignalTape;
