import React, { useMemo } from 'react';
import { useSettings } from '../context/SettingsContext';
import { TierBadge, formatCountdown as sharedCountdown } from '../utils/format';

interface Opportunity {
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_rate: number;
  short_rate: number;
  net_pct: number;
  gross_pct: number;
  funding_spread_pct?: number;
  immediate_spread_pct?: number;
  hourly_rate_pct?: number;
  min_interval_hours?: number;
  next_funding_ms?: number | null;
  long_next_funding_ms?: number | null;
  short_next_funding_ms?: number | null;
  long_interval_hours?: number;
  short_interval_hours?: number;
  qualified?: boolean;
  price: number;
  mode: string;
  fees_pct?: number;
  immediate_net_pct?: number;
  entry_tier?: string | null;
  price_spread_pct?: number | null;
}

interface RightPanelProps {
  opportunities: { opportunities: Opportunity[]; count: number } | null;
  status?: { min_funding_spread?: number; [key: string]: any } | null;
}

/* ── Mode badge config ────────────────────────────────────────── */
const MODE_MAP: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  cherry_pick: { icon: '🍒', label: 'CHERRY',     color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
  nutcracker:  { icon: '🥜', label: 'NUTCRACKER',  color: '#eab308', bg: 'rgba(234,179,8,0.10)' },
  pot:         { icon: '🍯', label: 'POT',         color: '#22c55e', bg: 'rgba(34,197,94,0.12)' },
};

const RightPanel: React.FC<RightPanelProps> = React.memo(({ opportunities, status }) => {
  const thresholdPct = status?.min_funding_spread != null
    ? `${status.min_funding_spread}%`
    : '?%';
  const { t } = useSettings();
  const opps = useMemo(() => opportunities?.opportunities ?? [], [opportunities]);
  const count = opportunities?.count ?? 0;

  const formatFunding = (rate: number): string => {
    const pct = Math.abs(rate) <= 1 ? rate * 100 : rate;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const formatSpread = (pct: number): string => {
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  const formatCountdown = sharedCountdown;

  const getLongRateStyle = (rate: number): React.CSSProperties => {
    if (rate < 0) return { color: 'var(--green)' };
    if (rate > 0) return { color: 'var(--red)' };
    return { color: 'var(--text-muted)' };
  };

  const getShortRateStyle = (rate: number): React.CSSProperties => {
    if (rate > 0) return { color: 'var(--green)' };
    if (rate < 0) return { color: 'var(--red)' };
    return { color: 'var(--text-muted)' };
  };

  const getSpreadColor = (pct: number): string => {
    if (pct > 0) return 'var(--green)';
    if (pct < 0) return 'var(--red)';
    return 'var(--text-muted)';
  };

  const tierBadge = (tier?: string | null) => <TierBadge tier={tier} t={t} />;

  const aboveThreshold = useMemo(() => opps.filter(o => o.qualified !== false), [opps]);
  const belowThreshold = useMemo(() => opps.filter(o => o.qualified === false), [opps]);

  /* ── Funding countdown cell with progress indicator ────────── */
  const renderFundingCell = (
    exchange: string,
    nextMs: number | null | undefined,
    intervalHours: number,
  ) => {
    const now = Date.now();
    const diff = nextMs ? nextMs - now : null;
    const isUrgent = diff !== null && diff < 900000;
    const isNear   = diff !== null && diff < 3600000;
    const countdown = formatCountdown(nextMs);

    // Progress: how far into the funding interval we are (0 → just reset, 1 → imminent)
    const progress = diff !== null
      ? Math.max(0, Math.min(1, 1 - diff / (intervalHours * 3600000)))
      : 0;
    const barColor = isUrgent ? '#10b981' : isNear ? '#f59e0b' : '#334155';

    return (
      <div className="nx-funding-cell">
        <div className="nx-funding-cell__header">
          <span className="nx-funding-cell__exch">
            {exchange.toUpperCase().slice(0, 3)}
          </span>
          <span className="nx-funding-cell__interval">
            {intervalHours}h
          </span>
        </div>
        <div className={`nx-funding-cell__time ${isUrgent ? 'nx-funding-cell__time--urgent' : isNear ? 'nx-funding-cell__time--near' : ''}`}>
          {countdown}
        </div>
        <div className="nx-funding-cell__bar">
          <div
            className="nx-funding-cell__bar-fill"
            style={{ width: `${progress * 100}%`, background: barColor }}
          />
        </div>
      </div>
    );
  };

  /* ── Mode badge pill ───────────────────────────────────────── */
  const renderModeBadge = (mode: string) => {
    const cfg = MODE_MAP[mode];
    if (!cfg) {
      return <span className="nx-mode-badge nx-mode-badge--hold">HOLD</span>;
    }
    return (
      <span className="nx-mode-badge" style={{
        color: cfg.color,
        background: cfg.bg,
        borderColor: cfg.color + '33',
      }}>
        <span className="nx-mode-badge__icon">{cfg.icon}</span>
        {cfg.label}
      </span>
    );
  };

  /* ── Single row ─────────────────────────────────────────────── */
  const renderRow = (opp: Opportunity, idx: number, dimmed: boolean) => {
    const immediateSpread = opp.immediate_spread_pct ?? 0;
    const longRate  = opp.long_rate  ?? 0;
    const shortRate = opp.short_rate ?? 0;
    const longIsIncome  = longRate  < 0;
    const shortIsIncome = shortRate > 0;
    const rowClass = dimmed
      ? 'nx-row nx-row--dimmed'
      : 'nx-row nx-row--qualified opp-row--qualified bridge-flow-active';

    const stableKey = `${opp.symbol}_${opp.long_exchange}_${opp.short_exchange}`;
    return (
      <tr
        key={stableKey}
        className={rowClass}
        style={{ animationDelay: `${idx * 35}ms` }}
      >
        {/* ── Symbol + tier + price spread ── */}
        <td className="nx-cell-pair">
          <div className="nx-pair-main">
            <span className={dimmed ? 'nx-dot nx-dot--dim' : 'nx-dot nx-dot--live'} />
            <span className="nx-pair-symbol">{opp.symbol}</span>
          </div>
          <div className="nx-pair-meta">
            {tierBadge(opp.entry_tier)}
            {opp.price_spread_pct != null && (
              <span className="nx-price-spread" style={{
                color: opp.price_spread_pct >= 0 ? 'var(--green)' : 'var(--red)',
              }}>
                P:{opp.price_spread_pct >= 0 ? '+' : ''}{opp.price_spread_pct.toFixed(2)}%
              </span>
            )}
          </div>
        </td>

        {/* ── Exchange bridge ── */}
        <td className="nx-cell-bridge">
          <div className="nx-bridge">
            <span className={`nx-bridge__exch ${longIsIncome ? 'nx-bridge__exch--earn' : ''}`}>
              {opp.long_exchange?.toUpperCase().slice(0, 3)}
              <span className={longIsIncome ? 'nx-arrow nx-arrow--up' : 'nx-arrow nx-arrow--down'}>
                {longIsIncome ? '▲' : '▼'}
              </span>
            </span>
            <span className="nx-bridge__line">
              <span className="nx-bridge__dot nx-bridge__dot--left" />
              <span className="nx-bridge__dot nx-bridge__dot--right" />
            </span>
            <span className={`nx-bridge__exch ${shortIsIncome ? 'nx-bridge__exch--earn' : ''}`}>
              {opp.short_exchange?.toUpperCase().slice(0, 3)}
              <span className={shortIsIncome ? 'nx-arrow nx-arrow--up' : 'nx-arrow nx-arrow--down'}>
                {shortIsIncome ? '▲' : '▼'}
              </span>
            </span>
          </div>
        </td>

        {/* ── Funding rates ── */}
        <td className="text-end mono nx-cell-rate" style={getLongRateStyle(longRate)}>
          {formatFunding(longRate)}
        </td>
        <td className="text-end mono nx-cell-rate" style={getShortRateStyle(shortRate)}>
          {formatFunding(shortRate)}
        </td>

        {/* ── Funding spread — pill highlight ── */}
        <td className="text-end mono nx-cell-spread">
          <span
            className="nx-spread-pill"
            style={{ color: getSpreadColor(immediateSpread) }}
          >
            {formatSpread(immediateSpread)}
          </span>
        </td>

        {/* ── Net profit — emphasized ── */}
        <td className="text-end mono nx-cell-net">
          <span
            className="nx-net-value"
            style={{ color: getSpreadColor(opp.net_pct ?? 0) }}
          >
            {opp.net_pct != null ? formatSpread(opp.net_pct) : '--'}
          </span>
        </td>

        {/* ── Mode badge ── */}
        <td className="text-end nx-cell-mode">
          {renderModeBadge(opp.mode)}
        </td>

        {/* ── Funding countdown ── */}
        <td className="nx-cell-funding">
          <div className="nx-funding-pair">
            {renderFundingCell(
              opp.long_exchange ?? '',
              opp.long_next_funding_ms,
              opp.long_interval_hours ?? 8,
            )}
            <div className="nx-funding-divider" />
            {renderFundingCell(
              opp.short_exchange ?? '',
              opp.short_next_funding_ms,
              opp.short_interval_hours ?? 8,
            )}
          </div>
        </td>
      </tr>
    );
  };

  /* ── Card + table ──────────────────────────────────────────── */
  return (
    <div className="card nx-opp-card flex flex-col" style={{ position: 'relative' }}>
      {/* Accent top glow */}
      <div className="nx-opp-card__glow" />

      {/* Header */}
      <div className="nx-opp-header card-header px-5 py-4 border-b" style={{ borderColor: 'var(--card-border)' }}>
        <div className="nx-opp-header__left">
          <svg className="nx-opp-header__icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
            <polyline points="16 7 22 7 22 13" />
          </svg>
          <span>{t.liveOpportunities}</span>
          <span className="card-header-muted">({count})</span>
        </div>
        {count > 0 && (
          <span className="xcard-live" style={{ marginInlineStart: 'auto' }}>
            <span className="xcard-live-dot" />LIVE
          </span>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto scrollbar-thin">
        {opps.length === 0 ? (
          <div className="nx-empty-state">
            <div className="nx-empty-state__pulse" />
            <span>{t.scanning}</span>
          </div>
        ) : (
          <table className="corp-table nx-table">
            <thead>
              <tr>
                <th>{t.pair}</th>
                <th>{t.colBridge}</th>
                <th className="text-end">{t.fundingL}</th>
                <th className="text-end">{t.fundingS}</th>
                <th className="text-end">{t.immediateSpreadOpp}</th>
                <th className="text-end">{t.netPct}</th>
                <th className="text-end">{t.colMode}</th>
                <th className="text-end">{t.colNextFunding}</th>
              </tr>
            </thead>
            <tbody>
              {aboveThreshold.map((opp, i) => renderRow(opp, i, false))}
              {aboveThreshold.length > 0 && belowThreshold.length > 0 && (
                <tr className="nx-separator-row">
                  <td colSpan={8}>
                    <div className="nx-separator">
                      <span className="nx-separator__line" />
                      <span className="nx-separator__label">
                        {t.belowThresholdLabel} ({thresholdPct})
                      </span>
                      <span className="nx-separator__line" />
                    </div>
                  </td>
                </tr>
              )}
              {belowThreshold.map((opp, i) => renderRow(opp, i + aboveThreshold.length, true))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
});

export default RightPanel;
