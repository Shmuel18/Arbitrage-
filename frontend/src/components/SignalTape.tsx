import React, { memo, useMemo } from 'react';

interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

interface SignalTapeProps {
  logs: LogEntry[];
  /** When provided, clicking a signal item scrolls the dashboard to the
   *  most relevant section. Called with a SECTION_IDS value string. */
  onSignalClick?: (sectionId: string) => void;
}

const LEVEL_COLOR: Record<string, string> = {
  ERROR:   '#ef4444',
  WARNING: '#f59e0b',
  INFO:    '#06b6d4',
  DEBUG:   '#475569',
};

const levelColor = (level: string): string =>
  LEVEL_COLOR[level.toUpperCase()] ?? '#475569';

/** Semantic pattern matching — turns raw log text into structured signal items. */
const SIGNAL_PATTERNS: { re: RegExp; icon: string; color: string }[] = [
  { re: /enter|open|entered|opening/i,       icon: '⚡', color: '#10b981' },
  { re: /exit|clos|closing/i,                icon: '🔒', color: '#f59e0b' },
  { re: /funding.*(\+[\d.]+%|collect)/i,     icon: '💰', color: '#d4af37' },
  { re: /profit|pnl.*\+/i,                   icon: '⬆️', color: '#10b981' },
  { re: /loss|pnl.*-/i,                      icon: '⬇️', color: '#ef4444' },
  { re: /error|fail|exception/i,             icon: '🚨', color: '#ef4444' },
  { re: /warn/i,                             icon: '⚠️', color: '#f59e0b' },
  { re: /scan/i,                             icon: '🔍', color: '#3b82f6' },
  { re: /connect/i,                          icon: '🔗', color: '#06b6d4' },
];

function parseSignal(log: LogEntry): { icon: string; text: string; color: string } {
  const msg = log.message;
  for (const p of SIGNAL_PATTERNS) {
    if (p.re.test(msg)) {
      // Extract symbol if present: e.g. BTC/USDT, ETH/USDT:USDT
      const symbolMatch = msg.match(/([A-Z]{2,10}\/USDT(?::USDT)?)/);
      const symbol = symbolMatch ? ` ${symbolMatch[1].replace('/USDT:USDT', '').replace('/USDT', '')}` : '';
      // Extract percentage if present
      const pctMatch = msg.match(/([-+]?[\d.]+%)/);
      const pct = pctMatch ? ` ${pctMatch[1]}` : '';
      // Build a concise signal: icon + symbol + pct, else truncated message
      const short = symbol || pct ? `${symbol}${pct}`.trim() : msg.slice(0, 40);
      return { icon: p.icon, text: short, color: p.color };
    }
  }
  // Fallback: use level color, truncate message
  return { icon: '·', text: msg.slice(0, 48), color: levelColor(log.level) };
}

/** Map a log message to the most relevant dashboard section id. */
function inferSection(log: LogEntry): string {
  const msg = log.message;
  if (/position|open|clos|entry|exit|long|short/i.test(msg))    return 'positions';
  if (/opportunit|funding|spread/i.test(msg))                    return 'opportunities';
  if (/trade|filled|execut/i.test(msg))                          return 'trades';
  if (/balance|deposit|withdraw/i.test(msg))                     return 'balances';
  if (/error|warn|fail|exception/i.test(msg))                    return 'logs';
  return 'dashboard';
}

/** Compute a scroll duration that stays visually comfortable regardless of
 *  how many items are in the tape. Roughly 3s per item, clamped 15–120s. */
function computeTapeDuration(itemCount: number): string {
  const secs = Math.max(15, Math.min(120, itemCount * 3));
  return `${secs}s`;
}

const SignalTape: React.FC<SignalTapeProps> = memo(({ logs, onSignalClick }) => {
  // Take the 30 most recent entries; memoize so parent re-renders that don't
  // change logs don't trigger JSX re-creation of the 60 rendered spans.
  const items = useMemo(() => (logs ?? []).slice(0, 30), [logs]);

  const tapeDuration = useMemo(() => computeTapeDuration(items.length), [items.length]);

  const isInteractive = Boolean(onSignalClick);

  // Pre-render both duplicated tape arrays (a + b). Memoized so they only
  // rebuild when the log content actually changes.
  const [renderedA, renderedB] = useMemo<[React.ReactNode, React.ReactNode]>(() => {
    const makeItems = (keyPrefix: string) =>
      items.map((log, i) => {
        const { icon, text, color } = parseSignal(log);
        return (
          <span
            key={`${keyPrefix}-${i}`}
            className={`signal-tape__item${isInteractive ? ' signal-tape__item--nav' : ''}`}
            style={{ color }}
            onClick={isInteractive ? () => onSignalClick!(inferSection(log)) : undefined}
            title={isInteractive ? log.message : undefined}
          >
            <span className="signal-tape__dot" style={{ background: color }} />
            <span style={{ marginInlineEnd: 3, fontSize: 11 }}>{icon}</span>
            {text}
            <span className="signal-tape__sep">·</span>
          </span>
        );
      });
    return [makeItems('a'), makeItems('b')];
  }, [items, isInteractive, onSignalClick]);

  if (items.length === 0) return null;

  return (
    <div className="signal-tape" role={isInteractive ? 'navigation' : undefined} aria-label={isInteractive ? 'Activity feed — click to scroll to section' : undefined}>
      <div className="signal-tape__label">FEED</div>
      <div className="signal-tape__track">
        <div
          className="signal-tape__inner"
          style={{ '--tape-duration': tapeDuration } as React.CSSProperties}
        >
          {renderedA}
          {renderedB}
        </div>
      </div>
    </div>
  );
});

SignalTape.displayName = 'SignalTape';

export default SignalTape;
