import React, { useMemo, useRef, useState, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useSettings } from '../context/SettingsContext';
import { TierBadge, formatCountdown, formatFundingRateN } from '../utils/format';
import { SkeletonRightPanel } from './Skeleton';
import type { Opportunity, OpportunitySet } from '../hooks/useMarketReducer';

/* ── Virtual row descriptor ───────────────────────────────────── */
type RowItem =
  | { type: 'qualified'; opp: Opportunity; idx: number }
  | { type: 'separator' }
  | { type: 'dimmed'; opp: Opportunity; idx: number };

/** Minimum row count before virtual rendering kicks in */
const VIRTUAL_THRESHOLD = 30;

interface RightPanelProps {
  opportunities: OpportunitySet | null;
  status?: { min_funding_spread?: number; [key: string]: any } | null;
}

/* ── Mode badge config ────────────────────────────────────────── */
const MODE_MAP: Record<string, { icon: string; tKey: string; fallback: string; color: string; bg: string }> = {
  cherry_pick: { icon: '🍒', tKey: 'cherry_pick', fallback: 'CHERRY',     color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
  nutcracker:  { icon: '🥜', tKey: 'nutcracker',  fallback: 'NUTCRACKER',  color: '#eab308', bg: 'rgba(234,179,8,0.10)' },
  pot:         { icon: '🍯', tKey: 'pot',         fallback: 'POT',         color: '#22c55e', bg: 'rgba(34,197,94,0.12)' },
};

/* ── Opportunity table column definitions ────────────────────── */
interface ColumnDef {
  /** Translation key from the settings context `t` object. */
  tKey: string;
  align: 'start' | 'end';
}

/** Single source of truth for the opportunities table column structure.
 *  Add, remove, or reorder columns here — the thead renders automatically. */
const OPP_COLUMNS: ColumnDef[] = [
  { tKey: 'pair',               align: 'start' },
  { tKey: 'colBridge',          align: 'start' },
  { tKey: 'fundingLS',          align: 'end'   },
  { tKey: 'immediateSpreadOpp', align: 'end'   },
  { tKey: 'netPct',             align: 'end'   },
  { tKey: 'colMode',            align: 'end'   },
  { tKey: 'colNextFunding',     align: 'end'   },
];

const RightPanel: React.FC<RightPanelProps> = React.memo(({ opportunities, status }) => {
  const thresholdPct = status?.min_funding_spread != null
    ? `${status.min_funding_spread}%`
    : '?%';
  const { t } = useSettings();

  // All hooks must be declared before any conditional return (Rules of Hooks).
  const opps = useMemo(() => opportunities?.opportunities ?? [], [opportunities]);
  const count = opportunities?.count ?? 0;
  const aboveThreshold = useMemo(() => opps.filter(o => o.qualified !== false), [opps]);
  const belowThreshold = useMemo(() => opps.filter(o => o.qualified === false), [opps]);

  // Flat row list for the virtualizer (qualified + separator + dimmed)
  const allRows = useMemo((): RowItem[] => [
    ...aboveThreshold.map((opp, i) => ({ type: 'qualified' as const, opp, idx: i })),
    ...(aboveThreshold.length > 0 && belowThreshold.length > 0
      ? [{ type: 'separator' as const }]
      : []),
    ...belowThreshold.map((opp, i) => ({ type: 'dimmed' as const, opp, idx: i + aboveThreshold.length })),
  ], [aboveThreshold, belowThreshold]);

  const parentRef = useRef<HTMLDivElement>(null);
  // Track known qualified opportunity keys — new ones glow on entry.
  const knownOppKeys = useRef<Set<string> | null>(null);

  // Departure burst: fires when a qualified opp vanishes (traded / dropped below threshold)
  const [bursts, setBursts] = useState<{ id: number; symbol: string }[]>([]);
  const burstIdRef = useRef(0);
  const prevAboveKeysRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    const currentKeys = new Set(
      aboveThreshold.map(o => `${o.symbol}_${o.long_exchange}_${o.short_exchange}`)
    );
    if (prevAboveKeysRef.current !== null) {
      const departed = [...prevAboveKeysRef.current].filter(k => !currentKeys.has(k));
      if (departed.length > 0) {
        const newBursts = departed.map(k => ({ id: ++burstIdRef.current, symbol: k.split('_')[0] }));
        setBursts(prev => [...prev, ...newBursts]);
        const ids = newBursts.map(b => b.id);
        setTimeout(() => setBursts(prev => prev.filter(b => !ids.includes(b.id))), 1600);
      }
    }
    prevAboveKeysRef.current = currentKeys;
  }, [aboveThreshold]);

  const rowVirtualizer = useVirtualizer({
    count: allRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => allRows[i]?.type === 'separator' ? 28 : 54,
    overscan: 8,
  });

  const isVirtual = allRows.length > VIRTUAL_THRESHOLD;

  // Show skeleton until the first opportunities payload arrives.
  if (opportunities === null) return <SkeletonRightPanel rows={8} />;

  // First render after data arrives: all current qualified opps are "known" — no flash.
  if (knownOppKeys.current === null) {
    knownOppKeys.current = new Set(
      aboveThreshold.map((o) => `${o.symbol}_${o.long_exchange}_${o.short_exchange}`),
    );
  }
  // Determine truly new qualified opportunities (not yet in the known set).
  const newOppKeys = new Set(
    aboveThreshold
      .filter((o) => !knownOppKeys.current!.has(`${o.symbol}_${o.long_exchange}_${o.short_exchange}`))
      .map((o) => `${o.symbol}_${o.long_exchange}_${o.short_exchange}`),
  );
  aboveThreshold.forEach((o) => knownOppKeys.current!.add(`${o.symbol}_${o.long_exchange}_${o.short_exchange}`));

  const formatFunding = (rate: number): string => formatFundingRateN(rate, 4);

  const formatSpread = (pct: number): string => {
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(4)}%`;
  };

  // formatCountdown from shared utils — imported directly above

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
      return <span className="nx-mode-badge nx-mode-badge--hold">{t.hold}</span>;
    }
    const label = (t as unknown as Record<string, string>)[cfg.tKey] ?? cfg.fallback;
    return (
      <span className="nx-mode-badge" style={{
        color: cfg.color,
        background: cfg.bg,
        borderColor: cfg.color + '33',
      }}>
        <span className="nx-mode-badge__icon">{cfg.icon}</span>
        {label}
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
    const isNewOpp = !dimmed && newOppKeys.has(stableKey);
    const finalRowClass = dimmed
      ? 'nx-row nx-row--dimmed'
      : isNewOpp
      ? 'nx-row nx-row--qualified opp-row--qualified bridge-flow-active nx-row--new-opp'
      : rowClass;
    return (
      <tr
        key={stableKey}
        className={finalRowClass}
        style={{ animationDelay: isNewOpp ? '0ms' : `${idx * 35}ms`, contain: 'layout' } as React.CSSProperties}
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
                color: opp.price_spread_pct > 0 ? 'var(--red)' : opp.price_spread_pct < 0 ? 'var(--green)' : 'var(--text-muted)',
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

        {/* ── Funding rates (long / short stacked) ── */}
        <td className="text-end nx-cell-rate-ls">
          <div style={getLongRateStyle(longRate)} className="mono">{formatFunding(longRate)}</div>
          <div style={getShortRateStyle(shortRate)} className="mono nx-cell-rate-ls__short">{formatFunding(shortRate)}</div>
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

        {/* ── Net profit — heat-fill bar behind the value ── */}
        <td className="text-end mono nx-cell-net">
          <div className="nx-net-heat" style={{
            '--heat-pct': `${Math.min(Math.abs(opp.net_pct ?? 0) / 0.5 * 100, 100)}%`,
            '--heat-color': getSpreadColor(opp.net_pct ?? 0),
          } as React.CSSProperties}>
            <span
              className="nx-net-value"
              style={{ color: getSpreadColor(opp.net_pct ?? 0) }}
            >
              {opp.net_pct != null ? formatSpread(opp.net_pct) : '--'}
            </span>
          </div>
        </td>

        {/* ── Mode badge ── */}
        <td className="text-end nx-cell-mode">
          {renderModeBadge(opp.mode ?? 'hold')}
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
            <span className="xcard-live-dot" />{t.live}
          </span>
        )}
      </div>

      {/* Table */}
      <div ref={parentRef} className="flex-1 overflow-auto scrollbar-thin" style={{ contain: 'content' }}>
        {opps.length === 0 ? (
          <div className="nx-empty-state">
            <div className="nx-empty-state__pulse" />
            <span>{t.scanning}</span>
          </div>
        ) : (
          <table className="corp-table nx-table">
            <thead>
              <tr>
                {OPP_COLUMNS.map((col) => (
                  <th key={col.tKey} className={col.align === 'end' ? 'text-end' : undefined}>
                    {(t as unknown as Record<string, string>)[col.tKey]}
                  </th>
                ))}
              </tr>
            </thead>
            {isVirtual ? (
              /* ── Virtual tbody: spacer-row pattern preserves column alignment ── */
              <tbody>
                {/* Top spacer */}
                <tr data-virtual-spacer="top">
                  <td
                    colSpan={7}
                    style={{ height: rowVirtualizer.getVirtualItems()[0]?.start ?? 0, padding: 0, border: 'none' }}
                  />
                </tr>
                {rowVirtualizer.getVirtualItems().map((vRow) => {
                  const item = allRows[vRow.index];
                  if (item.type === 'separator') {
                    return (
                      <tr key="virtual-separator" className="nx-separator-row">
                        <td colSpan={7}>
                          <div className="nx-separator">
                            <span className="nx-separator__line" />
                            <span className="nx-separator__label">
                              {t.belowThresholdLabel} ({thresholdPct})
                            </span>
                            <span className="nx-separator__line" />
                          </div>
                        </td>
                      </tr>
                    );
                  }
                  return renderRow(
                    item.opp,
                    item.idx,
                    item.type === 'dimmed',
                  );
                })}
                {/* Bottom spacer */}
                <tr data-virtual-spacer="bottom">
                  <td
                    colSpan={7}
                    style={{
                      height: rowVirtualizer.getTotalSize() - (rowVirtualizer.getVirtualItems().at(-1)?.end ?? 0),
                      padding: 0,
                      border: 'none',
                    }}
                  />
                </tr>
              </tbody>
            ) : (
              /* ── Standard tbody for small lists ── */
              <tbody>
                {aboveThreshold.map((opp, i) => renderRow(opp, i, false))}
                {aboveThreshold.length > 0 && belowThreshold.length > 0 && (
                  <tr className="nx-separator-row">
                    <td colSpan={7}>
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
            )}
          </table>
        )}
      </div>

      {/* Trade-fired burst notifications */}
      {bursts.map((b, bIdx) => (
        <div
          key={b.id}
          className="nx-opp-burst"
          style={{ top: `${52 + bIdx * 40}px` }}
          aria-hidden
        >
          <span className="nx-opp-burst__icon">⚡</span>
          <span className="nx-opp-burst__symbol">{b.symbol}</span>
          <span className="nx-opp-burst__label"> {t.tradeFired}</span>
        </div>
      ))}
    </div>
  );
});

export default RightPanel;
