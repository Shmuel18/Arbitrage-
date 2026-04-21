import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert } from '../types';
import { useSettings } from '../context/SettingsContext';

/**
 * AlertBell — inbox-style notification panel for the trading dashboard.
 *
 * Design standards (matches Phase 2–4 of the UI overhaul):
 *  - SVG icons, not emoji
 *  - Semantic intent tokens (--color-loss / --color-warning / --color-info)
 *  - CSS classes, not inline-style soup
 *  - ESC closes; click-outside closes; focus returns to trigger
 *  - Filter tabs: All / Unread / Critical
 *  - Alerts grouped by day (Today / Yesterday / Earlier)
 *  - Empty state matches the dashboard's polished style
 *  - RTL-aware via CSS, not JS-computed positions
 */

interface AlertBellProps {
  alerts: Alert[];
}

const ALERTS_LAST_SEEN_KEY = 'alerts_last_seen_ts';
type FilterMode = 'all' | 'unread' | 'critical';

/* ── SVG Icons ────────────────────────────────────────────────── */
const IconBell: React.FC<{ animated?: boolean }> = ({ animated }) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={animated ? 'nx-bell__icon-animated' : undefined}
    aria-hidden="true"
  >
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.73 21a2 2 0 0 1-3.46 0" />
  </svg>
);
const IconClose: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);
const IconInfo: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);
const IconWarn: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);
const IconCritical: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const SEVERITY_INTENT: Record<Alert['severity'], 'info' | 'warning' | 'critical'> = {
  info: 'info',
  warning: 'warning',
  critical: 'critical',
};

/* ── Utilities ────────────────────────────────────────────────── */

const timeFmt = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function startOfDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

interface DayGroup {
  label: 'today' | 'yesterday' | 'earlier';
  items: Alert[];
}

function groupByDay(alerts: Alert[]): DayGroup[] {
  const now = Date.now();
  const today = startOfDay(now);
  const yesterday = today - 24 * 60 * 60 * 1000;
  const buckets: Record<DayGroup['label'], Alert[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  };
  for (const a of alerts) {
    const t = Date.parse(a.timestamp);
    if (t >= today) buckets.today.push(a);
    else if (t >= yesterday) buckets.yesterday.push(a);
    else buckets.earlier.push(a);
  }
  const groups: DayGroup[] = [];
  if (buckets.today.length)     groups.push({ label: 'today',     items: buckets.today });
  if (buckets.yesterday.length) groups.push({ label: 'yesterday', items: buckets.yesterday });
  if (buckets.earlier.length)   groups.push({ label: 'earlier',   items: buckets.earlier });
  return groups;
}

/* ── Component ────────────────────────────────────────────────── */

const AlertBell: React.FC<AlertBellProps> = ({ alerts }) => {
  const { t, lang, isRtl } = useSettings();
  const [open, setOpen] = useState<boolean>(false);
  const [filter, setFilter] = useState<FilterMode>('all');
  const [seenTs, setSeenTs] = useState<number>(() => {
    const raw = window.localStorage.getItem(ALERTS_LAST_SEEN_KEY);
    return raw ? Number(raw) || 0 : 0;
  });
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const isUnread = useCallback(
    (a: Alert): boolean => Date.parse(a.timestamp) > seenTs,
    [seenTs]
  );

  const unreadCount = useMemo(() => alerts.filter(isUnread).length, [alerts, isUnread]);
  const criticalCount = useMemo(
    () => alerts.filter((a) => a.severity === 'critical').length,
    [alerts]
  );

  const filteredAlerts = useMemo(() => {
    if (filter === 'unread')   return alerts.filter(isUnread);
    if (filter === 'critical') return alerts.filter((a) => a.severity === 'critical');
    return alerts;
  }, [alerts, filter, isUnread]);

  const groups = useMemo(() => groupByDay(filteredAlerts), [filteredAlerts]);

  /* ── Formatting helpers ─────────────────────────────────────── */

  const formatCount = useCallback(
    (template: string, count: number): string => template.replace('{count}', String(count)),
    []
  );

  const formatRelativeTime = useCallback(
    (ts: string): string => {
      const diffMs = Date.now() - Date.parse(ts);
      if (diffMs < 5000) return t.timeJustNow;
      const sec = Math.floor(diffMs / 1000);
      if (sec < 60) return formatCount(t.timeSecondsAgo, sec);
      const min = Math.floor(sec / 60);
      if (min < 60) return formatCount(t.timeMinutesAgo, min);
      const hrs = Math.floor(min / 60);
      if (hrs < 24) return formatCount(t.timeHoursAgo, hrs);
      return formatCount(t.timeDaysAgo, Math.floor(hrs / 24));
    },
    [formatCount, t]
  );

  const formatAlertType = useCallback(
    (type: string): string => {
      const normalized = type.trim().toLowerCase();
      const knownHe: Record<string, string> = {
        info: 'מידע',
        warning: 'אזהרה',
        critical: 'קריטי',
        trade_open: 'פתיחת עסקה',
        trade_close: 'סגירת עסקה',
        panic_close: 'סגירת חירום',
        funding: 'מימון',
        liquidation: 'סיכון נזילות',
        risk: 'סיכון',
        scanner: 'סורק',
        websocket: 'וובסוקט',
        exchange: 'בורסה',
      };
      const knownEn: Record<string, string> = {
        info: 'Info',
        warning: 'Warning',
        critical: 'Critical',
        trade_open: 'Trade Open',
        trade_close: 'Trade Close',
        panic_close: 'Panic Close',
        funding: 'Funding',
        liquidation: 'Liquidation Risk',
        risk: 'Risk',
        scanner: 'Scanner',
        websocket: 'WebSocket',
        exchange: 'Exchange',
      };
      const known = lang === 'he' ? knownHe : knownEn;
      if (known[normalized]) return known[normalized];
      return type
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, (ch) => ch.toUpperCase());
    },
    [lang]
  );

  /* ── Actions ────────────────────────────────────────────────── */

  const markAllRead = useCallback((): void => {
    const now = Date.now();
    window.localStorage.setItem(ALERTS_LAST_SEEN_KEY, String(now));
    setSeenTs(now);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    // Return focus to the trigger for keyboard users
    triggerRef.current?.focus();
  }, []);

  /* ── Effects: ESC + click-outside ───────────────────────────── */

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
      }
    };
    const onClick = (e: MouseEvent) => {
      if (
        panelRef.current &&
        !panelRef.current.contains(e.target as Node) &&
        !triggerRef.current?.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };

    window.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      window.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open, close]);

  /* ── Labels for filter tabs & day groups ────────────────────── */

  const filterLabel = (f: FilterMode): string => {
    if (f === 'all')      return `${t.alertsFilterAll} (${alerts.length})`;
    if (f === 'unread')   return `${t.alertsFilterUnread} (${unreadCount})`;
    return `${t.alertsFilterCritical} (${criticalCount})`;
  };
  const groupLabel = (g: DayGroup['label']): string =>
    g === 'today' ? t.alertsToday : g === 'yesterday' ? t.alertsYesterday : t.alertsEarlier;

  /* ── Render ─────────────────────────────────────────────────── */

  const severityIcon = (sev: Alert['severity']): React.ReactNode => {
    if (sev === 'critical') return <IconCritical />;
    if (sev === 'warning')  return <IconWarn />;
    return <IconInfo />;
  };
  const severityLabel = (sev: Alert['severity']): string => {
    if (sev === 'critical') return t.alertSeverityCritical;
    if (sev === 'warning')  return t.alertSeverityWarning;
    return t.alertSeverityInfo;
  };

  return (
    <div className="nx-bell">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className={`nx-topbar-btn nx-bell__trigger${unreadCount > 0 ? ' nx-bell__trigger--has-unread' : ''}`}
        aria-label={t.alertsTitle}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={t.alertsTitle}
      >
        <IconBell animated={unreadCount > 0} />
        {unreadCount > 0 && (
          <span className="nx-bell__badge" aria-hidden="true">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Backdrop — visible on mobile only via CSS */}
          <div
            className="nx-bell__backdrop"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div
            ref={panelRef}
            className={`nx-bell__panel${isRtl ? ' nx-bell__panel--rtl' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label={t.alertsTitle}
            dir={isRtl ? 'rtl' : 'ltr'}
          >
            <header className="nx-bell__header">
              <div className="nx-bell__header-title">
                <h3>{t.alertsTitle}</h3>
                {unreadCount > 0 && (
                  <span className="nx-bell__new-count">
                    {unreadCount} {t.alertsNew}
                  </span>
                )}
              </div>
              <div className="nx-bell__header-actions">
                {unreadCount > 0 && (
                  <button
                    type="button"
                    className="nx-bell__mark-read"
                    onClick={markAllRead}
                  >
                    {t.alertsMarkAllRead}
                  </button>
                )}
                <button
                  type="button"
                  className="nx-bell__close"
                  onClick={close}
                  aria-label={t.closeDialog}
                >
                  <IconClose />
                </button>
              </div>
            </header>

            {/* Filter tabs */}
            <div className="nx-bell__filters" role="tablist" aria-label={t.alertsTitle}>
              {(['all', 'unread', 'critical'] as FilterMode[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  className={`nx-bell__filter${filter === f ? ' nx-bell__filter--active' : ''}`}
                  onClick={() => setFilter(f)}
                >
                  {filterLabel(f)}
                </button>
              ))}
            </div>

            {/* List */}
            <div className="nx-bell__list" tabIndex={0} role="region">
              {filteredAlerts.length === 0 ? (
                <div className="nx-empty-state nx-bell__empty">
                  <div className="nx-empty-state__icon" aria-hidden="true">
                    <IconBell />
                  </div>
                  <div className="nx-empty-state__title">
                    {filter === 'all' ? t.alertsEmpty :
                     filter === 'unread' ? t.alertsEmptyUnread :
                     t.alertsEmptyCritical}
                  </div>
                </div>
              ) : (
                groups.map((group) => (
                  <section key={group.label} className="nx-bell__group">
                    <h4 className="nx-bell__group-header">{groupLabel(group.label)}</h4>
                    <ul className="nx-bell__items">
                      {group.items.map((alert) => {
                        const intent = SEVERITY_INTENT[alert.severity];
                        const unread = isUnread(alert);
                        return (
                          <li
                            key={alert.id}
                            className={`nx-bell__item nx-bell__item--${intent}${unread ? ' nx-bell__item--unread' : ''}`}
                          >
                            <div className="nx-bell__item-severity" data-intent={intent}>
                              {severityIcon(alert.severity)}
                            </div>
                            <div className="nx-bell__item-body">
                              <div className="nx-bell__item-meta">
                                <span className={`nx-bell__severity-badge nx-bell__severity-badge--${intent}`}>
                                  {severityLabel(alert.severity)}
                                </span>
                                <span className="nx-bell__item-type">
                                  {formatAlertType(alert.type)}
                                </span>
                                <span
                                  className="nx-bell__item-time"
                                  title={new Date(alert.timestamp).toLocaleString()}
                                >
                                  {formatRelativeTime(alert.timestamp)}
                                </span>
                              </div>
                              <div className="nx-bell__item-message">
                                {alert.message}
                              </div>
                              {(alert.symbol || alert.exchange) && (
                                <div className="nx-bell__item-tags">
                                  {alert.symbol && (
                                    <span className="nx-bell__tag nx-bell__tag--symbol">
                                      {alert.symbol}
                                    </span>
                                  )}
                                  {alert.exchange && (
                                    <span className="nx-bell__tag">
                                      {alert.exchange}
                                    </span>
                                  )}
                                  <span className="nx-bell__exact-time">
                                    {timeFmt.format(new Date(alert.timestamp))}
                                  </span>
                                </div>
                              )}
                            </div>
                            {unread && (
                              <span
                                className="nx-bell__unread-dot"
                                aria-label="Unread"
                                title="Unread"
                              />
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ))
              )}
            </div>

            {alerts.length > 0 && (
              <footer className="nx-bell__footer">
                {formatCount(t.alertsFooter, alerts.length)}
              </footer>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default React.memo(AlertBell);
