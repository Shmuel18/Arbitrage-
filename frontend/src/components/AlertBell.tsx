import React, { useMemo, useState } from 'react';
import { Alert } from '../types';

interface AlertBellProps {
  alerts: Alert[];
}

const ALERTS_LAST_SEEN_KEY = 'alerts_last_seen_ts';

const AlertBell: React.FC<AlertBellProps> = ({ alerts }) => {
  const [open, setOpen] = useState<boolean>(false);
  const [seenTs, setSeenTs] = useState<number>(() => {
    const raw = window.localStorage.getItem(ALERTS_LAST_SEEN_KEY);
    return raw ? Number(raw) || 0 : 0;
  });

  const unreadCount = useMemo(() => {
    return alerts.filter((alert) => Date.parse(alert.timestamp) > seenTs).length;
  }, [alerts, seenTs]);

  const markAllRead = (): void => {
    const now = Date.now();
    window.localStorage.setItem(ALERTS_LAST_SEEN_KEY, String(now));
    setSeenTs(now);
  };

  const severityColor = (severity: Alert['severity']): string => {
    if (severity === 'critical') {
      return '#ef4444';
    }
    if (severity === 'warning') {
      return '#f59e0b';
    }
    return '#10b981';
  };

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="nx-topbar-btn"
        title="Alerts"
        aria-label="Alerts"
      >
        🔔
        {unreadCount > 0 && (
          <span
            style={{
              marginInlineStart: 6,
              background: '#ef4444',
              color: '#fff',
              borderRadius: 999,
              padding: '0 6px',
              fontSize: 11,
              lineHeight: '16px',
              minWidth: 16,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 8px)',
            right: 0,
            width: 420,
            maxHeight: 360,
            overflowY: 'auto',
            zIndex: 70,
            background: 'var(--panel-bg)',
            border: '1px solid var(--border-color)',
            borderRadius: 12,
            padding: 10,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <strong>Alerts</strong>
            <button onClick={markAllRead} className="nx-topbar-btn" style={{ height: 28, minHeight: 28 }}>
              Mark all read
            </button>
          </div>

          {alerts.length === 0 ? (
            <div style={{ opacity: 0.75, padding: '8px 4px' }}>No alerts in last 24h.</div>
          ) : (
            alerts.map((alert) => (
              <div
                key={alert.id}
                style={{
                  borderTop: '1px solid var(--border-color)',
                  padding: '8px 4px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 3,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <span style={{ color: severityColor(alert.severity), fontWeight: 700 }}>
                    {alert.severity.toUpperCase()} · {alert.type}
                  </span>
                  <span style={{ opacity: 0.65, fontSize: 12 }}>
                    {new Date(alert.timestamp).toLocaleString()}
                  </span>
                </div>
                <div style={{ fontSize: 13 }}>{alert.message}</div>
                {(alert.symbol || alert.exchange) && (
                  <div style={{ opacity: 0.65, fontSize: 12 }}>
                    {[alert.symbol, alert.exchange].filter(Boolean).join(' · ')}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};

export default React.memo(AlertBell);
