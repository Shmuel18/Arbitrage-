import React from 'react';

/* ── Shared currency formatter (module-level singleton) ────────── */
const _usdFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
});

const _usdFmt4 = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

export const formatCurrency = (value: number): string => _usdFmt.format(value);
export const formatCurrency4 = (value: number): string => _usdFmt4.format(value);

/* ── Tier badge (used in PositionsTable, RecentTradesPanel, RightPanel) ── */
interface TierInfo {
  emoji: string;
  label: string;
  color: string;
  bg: string;
}

interface TierTranslations {
  tierTop?: string;
  tierMedium?: string;
  tierBad?: string;
  tierAdverse?: string;
}

export const getTierInfo = (tier: string | null | undefined, t: TierTranslations): TierInfo | null => {
  if (!tier) return null;
  const key = tier.toLowerCase();
  if (key === 'top')     return { emoji: '🏆 ', label: t.tierTop     ?? 'TOP',     color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' };
  if (key === 'medium')  return { emoji: '📊 ', label: t.tierMedium  ?? 'MEDIUM',  color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' };
  if (key === 'bad')     return { emoji: '⚠️ ', label: t.tierBad     ?? 'BAD',     color: '#ef4444', bg: 'rgba(239,68,68,0.12)' };
  if (key === 'adverse') return { emoji: '',     label: t.tierAdverse ?? 'ADVERSE', color: '#6b7280', bg: 'rgba(107,114,128,0.12)' };
  return null;
};

export const TierBadge: React.FC<{ tier?: string | null; t: TierTranslations }> = ({ tier, t }) => {
  const info = getTierInfo(tier, t);
  if (!info) return null;
  return (
    <span style={{
      background: info.color + '18',
      color: info.color,
      border: `1px solid ${info.color}44`,
      borderRadius: 4,
      padding: '0px 6px',
      fontSize: 10,
      fontWeight: 700,
      letterSpacing: '0.06em',
      marginInlineStart: 4,
    }}>
      {info.emoji}{info.label}
    </span>
  );
};

/* ── Countdown formatter (used in PositionsTable, RightPanel) ──── */
export const formatCountdown = (ms?: number | null): string => {
  if (!ms) return '--';
  const diff = ms - Date.now();
  if (diff <= 0) return '⚡ NOW';
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs}h${rem > 0 ? rem + 'm' : ''}`;
};

/* ── Numeric helpers ─────────────────────────────────────────────── */
export const parseNum = (v?: string | null): number | null => {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
};

export const formatPct = (v?: string | null, decimals = 3): string => {
  const n = parseNum(v);
  if (n == null) return '--';
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`;
};

export const formatFundingRate = (rate?: string | null): string => {
  if (!rate) return '--';
  const n = Number(rate);
  if (Number.isNaN(n)) return '--';
  const pct = Math.abs(n) <= 1 ? n * 100 : n;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(3)}%`;
};
