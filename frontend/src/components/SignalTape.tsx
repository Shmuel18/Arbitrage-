import React, { memo, useMemo } from 'react';

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
  INFO:    '#2DB8C4',
  DEBUG:   '#475569',
};

const levelColor = (level: string): string =>
  LEVEL_COLOR[level.toUpperCase()] ?? '#475569';

/** Compute a scroll duration that stays visually comfortable regardless of
 *  how many items are in the tape. Roughly 3s per item, clamped 15–120s. */
function computeTapeDuration(itemCount: number): string {
  const secs = Math.max(15, Math.min(120, itemCount * 3));
  return `${secs}s`;
}

/**
 * Merge consecutive duplicate messages into a single item with a ×N counter.
 * Example: ["Top 5 updated", "Top 5 updated", "Top 5 updated", "Error X"]
 *       => [{ message: "Top 5 updated", count: 3 }, { message: "Error X", count: 1 }]
 * After merge we cap to `maxItems` to keep the tape readable.
 */
interface DedupedItem {
  message: string;
  level: string;
  count: number;
}

function dedupeConsecutive(logs: LogEntry[], maxItems = 12): DedupedItem[] {
  const result: DedupedItem[] = [];
  for (const log of logs) {
    const last = result[result.length - 1];
    if (last && last.message === log.message && last.level === log.level) {
      last.count += 1;
    } else {
      result.push({ message: log.message, level: log.level, count: 1 });
    }
    if (result.length >= maxItems) break;
  }
  return result;
}

const SignalTape: React.FC<SignalTapeProps> = memo(({ logs }) => {
  // Dedupe before slicing: collapse repeats so ticker is informative, not noisy.
  const items = useMemo(() => dedupeConsecutive(logs ?? [], 12), [logs]);

  const tapeDuration = useMemo(() => computeTapeDuration(items.length), [items.length]);

  if (items.length === 0) return null;

  // Duplicate items so the marquee loops seamlessly:
  // The inner container is 200% wide, second half mirrors the first.
  const renderItems = (keyPrefix: string) =>
    items.map((log, i) => {
      const color = levelColor(log.level);
      return (
        <span key={`${keyPrefix}-${i}`} className="signal-tape__item" style={{ color }}>
          <span className="signal-tape__dot" style={{ background: color }} />
          {log.message}
          {log.count > 1 && (
            <span className="signal-tape__count" style={{ color }}>×{log.count}</span>
          )}
          <span className="signal-tape__sep">·</span>
        </span>
      );
    });

  return (
    <div className="signal-tape" aria-hidden="true">
      <div className="signal-tape__label">FEED</div>
      <div className="signal-tape__track">
        <div
          className="signal-tape__inner"
          style={{ '--tape-duration': tapeDuration } as React.CSSProperties}
        >
          {renderItems('a')}
          {renderItems('b')}
        </div>
      </div>
    </div>
  );
});

SignalTape.displayName = 'SignalTape';

export default SignalTape;
