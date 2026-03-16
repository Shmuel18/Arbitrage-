import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert } from '../types';

interface AlertBellProps {
  alerts: Alert[];
}

const ALERTS_LAST_SEEN_KEY = 'alerts_last_seen_ts';

/* ── Severity config ────────────────────────────────────────────── */
interface SeverityCfg {
  icon: string;
  color: string;
  bg: string;
  border: string;
  label: string;
}

const SEVERITY: Record<Alert['severity'], SeverityCfg> = {
  critical: {
    icon: '🔴',
    color: 'var(--red, #ef4444)',
    bg: 'var(--red-bg, rgba(239,68,68,0.08))',
    border: 'var(--red-border, rgba(239,68,68,0.2))',
    label: 'CRITICAL',
  },
  warning: {
    icon: '🟡',
    color: 'var(--yellow, #f59e0b)',
    bg: 'var(--yellow-bg, rgba(245,158,11,0.08))',
    border: 'rgba(245,158,11,0.2)',
    label: 'WARNING',
  },
  info: {
    icon: '🔵',
    color: 'var(--accent, #1d6fe8)',
    bg: 'var(--accent-light, rgba(29,111,232,0.08))',
    border: 'var(--accent-border, rgba(29,111,232,0.2))',
    label: 'INFO',
  },
};

/* ── Time formatting ────────────────────────────────────────────── */
const timeAgo = (ts: string): string => {
  const diffMs = Date.now() - Date.parse(ts);
  if (diffMs < 0) return 'just now';
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
};

const timeFmt = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

/* ── Component ──────────────────────────────────────────────────── */
const AlertBell: React.FC<AlertBellProps> = ({ alerts }) => {
  const [open, setOpen] = useState<boolean>(false);
  const [seenTs, setSeenTs] = useState<number>(() => {
    const raw = window.localStorage.getItem(ALERTS_LAST_SEEN_KEY);
    return raw ? Number(raw) || 0 : 0;
  });
  const panelRef = useRef<HTMLDivElement>(null);

  const unreadCount = useMemo(() => {
    return alerts.filter((a) => Date.parse(a.timestamp) > seenTs).length;
  }, [alerts, seenTs]);

  const markAllRead = useCallback((): void => {
    const now = Date.now();
    window.localStorage.setItem(ALERTS_LAST_SEEN_KEY, String(now));
    setSeenTs(now);
  }, []);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent): void => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const isUnread = (a: Alert): boolean => Date.parse(a.timestamp) > seenTs;

  return (
    <div style={{ position: 'relative' }} ref={panelRef}>
      {/* ── Bell button ──────────────────────────────── */}
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="nx-topbar-btn"
        title="Alerts"
        aria-label="Alerts"
        style={{ position: 'relative' }}
      >
        🔔
        {unreadCount > 0 && (
          <span
            style={{
              position: 'absolute',
              top: 2,
              right: 0,
              background: 'var(--red, #ef4444)',
              color: '#fff',
              borderRadius: 'var(--radius-full, 9999px)',
              padding: '0 5px',
              fontSize: 'var(--text-2xs, 0.65rem)',
              fontWeight: 'var(--fw-bold, 700)' as any,
              lineHeight: '16px',
              minWidth: 16,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 0 0 2px var(--nav-bg, #111827)',
              animation: unreadCount > 0 ? 'alert-badge-pulse 2s ease-in-out infinite' : undefined,
            }}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* ── Dropdown panel ───────────────────────────── */}
      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 10px)',
            right: -8,
            width: 400,
            maxHeight: 480,
            display: 'flex',
            flexDirection: 'column',
            zIndex: 70,
            background: 'var(--card-bg, #fff)',
            border: '1px solid var(--card-border, rgba(0,0,0,0.06))',
            borderRadius: 'var(--radius-lg, 14px)',
            boxShadow:
              '0 20px 60px -12px rgba(0,0,0,0.25), 0 0 0 1px rgba(96,165,250,0.05)',
            overflow: 'hidden',
          }}
        >
          {/* ── Header ─────────────────────────────────── */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '14px 16px 12px',
              borderBottom: '1px solid var(--divider, #e2e8f0)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 'var(--text-base, 0.875rem)', fontWeight: 'var(--fw-semibold, 600)' as any, color: 'var(--text-primary)' }}>
                Notifications
              </span>
              {unreadCount > 0 && (
                <span
                  style={{
                    background: 'var(--accent, #1d6fe8)',
                    color: '#fff',
                    borderRadius: 'var(--radius-full, 9999px)',
                    padding: '1px 8px',
                    fontSize: 'var(--text-2xs, 0.65rem)',
                    fontWeight: 'var(--fw-semibold, 600)' as any,
                  }}
                >
                  {unreadCount} new
                </span>
              )}
            </div>
            {unreadCount > 0 && (
              <button
                onClick={markAllRead}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--accent, #1d6fe8)',
                  fontSize: 'var(--text-xs, 0.72rem)',
                  fontWeight: 'var(--fw-medium, 500)' as any,
                  cursor: 'pointer',
                  padding: '4px 8px',
                  borderRadius: 'var(--radius-sm, 4px)',
                  transition: 'background var(--duration-fast, 150ms)',
                }}
                onMouseEnter={(e) => { (e.target as HTMLElement).style.background = 'var(--accent-light, rgba(29,111,232,0.08))'; }}
                onMouseLeave={(e) => { (e.target as HTMLElement).style.background = 'none'; }}
              >
                Mark all read
              </button>
            )}
          </div>

          {/* ── Alert list ─────────────────────────────── */}
          <div
            style={{
              overflowY: 'auto',
              flex: 1,
              padding: '4px 0',
            }}
          >
            {alerts.length === 0 ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '40px 16px',
                  gap: 8,
                }}
              >
                <span style={{ fontSize: 28, opacity: 0.4 }}>🔔</span>
                <span style={{ color: 'var(--text-muted, #94a3b8)', fontSize: 'var(--text-sm, 0.82rem)' }}>
                  No alerts in the last 24 hours
                </span>
              </div>
            ) : (
              alerts.map((alert) => {
                const cfg = SEVERITY[alert.severity];
                const unread = isUnread(alert);
                return (
                  <div
                    key={alert.id}
                    style={{
                      display: 'flex',
                      gap: 12,
                      padding: '12px 16px',
                      margin: '0 6px',
                      borderRadius: 'var(--radius-md, 8px)',
                      background: unread ? cfg.bg : 'transparent',
                      borderLeft: `3px solid ${unread ? cfg.color : 'transparent'}`,
                      transition: 'background var(--duration-fast, 150ms)',
                      cursor: 'default',
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'var(--table-hover, rgba(37,99,235,0.035))'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = unread ? cfg.bg : 'transparent'; }}
                  >
                    {/* Severity icon */}
                    <div
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: 'var(--radius-full, 9999px)',
                        background: cfg.bg,
                        border: `1px solid ${cfg.border}`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 14,
                        flexShrink: 0,
                        marginTop: 1,
                      }}
                    >
                      {cfg.icon}
                    </div>

                    {/* Content */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {/* Top row: type + time */}
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          gap: 8,
                          marginBottom: 3,
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span
                            style={{
                              fontSize: 'var(--text-2xs, 0.65rem)',
                              fontWeight: 'var(--fw-bold, 700)' as any,
                              color: cfg.color,
                              textTransform: 'uppercase',
                              letterSpacing: '0.04em',
                            }}
                          >
                            {cfg.label}
                          </span>
                          <span
                            style={{
                              fontSize: 'var(--text-2xs, 0.65rem)',
                              color: 'var(--text-muted, #94a3b8)',
                            }}
                          >
                            ·
                          </span>
                          <span
                            style={{
                              fontSize: 'var(--text-xs, 0.72rem)',
                              fontWeight: 'var(--fw-medium, 500)' as any,
                              color: 'var(--text-secondary, #64748b)',
                            }}
                          >
                            {alert.type}
                          </span>
                        </div>
                        <span
                          style={{
                            fontSize: 'var(--text-2xs, 0.65rem)',
                            color: 'var(--text-muted, #94a3b8)',
                            whiteSpace: 'nowrap',
                            fontVariantNumeric: 'tabular-nums',
                          }}
                          title={new Date(alert.timestamp).toLocaleString()}
                        >
                          {timeAgo(alert.timestamp)}
                        </span>
                      </div>

                      {/* Message */}
                      <div
                        style={{
                          fontSize: 'var(--text-sm, 0.82rem)',
                          color: 'var(--text-primary)',
                          lineHeight: 1.45,
                          wordBreak: 'break-word',
                        }}
                      >
                        {alert.message}
                      </div>

                      {/* Tags: symbol + exchange */}
                      {(alert.symbol || alert.exchange) && (
                        <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                          {alert.symbol && (
                            <span
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 3,
                                fontSize: 'var(--text-2xs, 0.65rem)',
                                fontWeight: 'var(--fw-medium, 500)' as any,
                                color: 'var(--text-secondary, #64748b)',
                                background: 'var(--table-stripe, #fafcfe)',
                                border: '1px solid var(--table-border, #f1f5f9)',
                                borderRadius: 'var(--radius-sm, 4px)',
                                padding: '2px 7px',
                                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                              }}
                            >
                              {alert.symbol}
                            </span>
                          )}
                          {alert.exchange && (
                            <span
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 3,
                                fontSize: 'var(--text-2xs, 0.65rem)',
                                fontWeight: 'var(--fw-medium, 500)' as any,
                                color: 'var(--text-secondary, #64748b)',
                                background: 'var(--table-stripe, #fafcfe)',
                                border: '1px solid var(--table-border, #f1f5f9)',
                                borderRadius: 'var(--radius-sm, 4px)',
                                padding: '2px 7px',
                              }}
                            >
                              {alert.exchange}
                            </span>
                          )}
                          <span
                            style={{
                              fontSize: 'var(--text-2xs, 0.65rem)',
                              color: 'var(--text-muted, #94a3b8)',
                              alignSelf: 'center',
                              fontVariantNumeric: 'tabular-nums',
                            }}
                          >
                            {timeFmt.format(new Date(alert.timestamp))}
                          </span>
                        </div>
                      )}
                    </div>

                    {/* Unread dot */}
                    {unread && (
                      <div
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: 'var(--radius-full, 9999px)',
                          background: 'var(--accent, #1d6fe8)',
                          flexShrink: 0,
                          marginTop: 6,
                        }}
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>

          {/* ── Footer ─────────────────────────────────── */}
          {alerts.length > 0 && (
            <div
              style={{
                borderTop: '1px solid var(--divider, #e2e8f0)',
                padding: '8px 16px',
                display: 'flex',
                justifyContent: 'center',
              }}
            >
              <span
                style={{
                  fontSize: 'var(--text-2xs, 0.65rem)',
                  color: 'var(--text-muted, #94a3b8)',
                }}
              >
                Showing last {alerts.length} alert{alerts.length !== 1 ? 's' : ''} (24h)
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── Badge pulse animation ──────────────────── */}
      <style>{`
        @keyframes alert-badge-pulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); }
        }
      `}</style>
    </div>
  );
};

export default React.memo(AlertBell);
