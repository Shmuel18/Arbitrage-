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

/* ── Funding rate with configurable decimal precision ────────────── */
export const formatFundingRateN = (rate?: string | number | null, decimals = 4): string => {
  if (rate == null || rate === '') return '--';
  const n = Number(rate);
  if (Number.isNaN(n)) return '--';
  const pct = Math.abs(n) <= 1 ? n * 100 : n;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(decimals)}%`;
};

/* ── USD formatter (configurable fractions) ─────────────────────── */
export const formatUsd = (value?: string | number | null, fractions = 2): string => {
  if (value == null || value === '') return '--';
  const n = Number(value);
  if (Number.isNaN(n)) return '--';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractions,
    maximumFractionDigits: fractions,
  }).format(n);
};

/* ── Price formatter (auto-precision based on magnitude) ────────── */
export const formatPrice = (v?: string | number | null): string => {
  const n = parseNum(typeof v === 'number' ? String(v) : v);
  if (n == null) return '--';
  if (n < 0.01) return n.toPrecision(4);
  if (n < 1) return n.toFixed(4);
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
};

/* ── Date/time formatter ─────────────────────────────────────────── */
const _dateFmt = new Intl.DateTimeFormat('default', {
  month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
});

const _dateFmtFull = new Intl.DateTimeFormat('default', {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
});

export const formatDate = (value?: string | null, includeYear = false): string => {
  if (!value) return '--';
  try {
    return (includeYear ? _dateFmtFull : _dateFmt).format(new Date(value));
  } catch {
    return '--';
  }
};

/* ── Duration formatter (minutes → "Xh Ym" or "Zm") ──────────────── */
export const formatDuration = (mins?: number | null): string => {
  if (mins == null) return '--';
  if (mins < 60) return `${Math.round(mins)}m`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
};

/* ── PnL colour helper ───────────────────────────────────────────── */
export const pnlColor = (
  v?: string | number | null,
  muted = 'var(--text-muted)',
): string => {
  const n = typeof v === 'number' ? v : parseNum(typeof v === 'string' ? v : null);
  if (n == null) return muted;
  return n >= 0 ? 'var(--green)' : 'var(--red)';
};

/* ── Mode badge pill ─────────────────────────────────────────────── */
interface ModeTranslations {
  cherry_pick?: string;
  pot?: string;
  nutcracker?: string;
  hold?: string;
}

interface ModeConfig {
  emoji: string;
  label: string;
  color: string;
  bg: string;
  border: string;
}

export const getModeConfig = (mode?: string | null, t: ModeTranslations = {}): ModeConfig => {
  const m = (mode ?? '').toLowerCase();
  if (m === 'cherry_pick') return { emoji: '🍒', label: t.cherry_pick ?? 'CHERRY PICK', color: '#f97316', bg: 'rgba(249,115,22,0.10)', border: 'rgba(249,115,22,0.35)' };
  if (m === 'pot')         return { emoji: '🍯', label: t.pot ?? 'POT',             color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.40)' };
  if (m === 'nutcracker')  return { emoji: '🔨🥜', label: t.nutcracker ?? 'NUTCRACKER', color: '#a855f7', bg: 'rgba(168,85,247,0.08)', border: 'rgba(168,85,247,0.35)' };
  return { emoji: '🤝', label: t.hold ?? 'HOLD', color: '#22c55e', bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.35)' };
};

export const ModeBadge: React.FC<{ mode?: string | null; t?: ModeTranslations }> = ({ mode, t = {} }) => {
  const cfg = getModeConfig(mode, t);
  return (
    <span style={{
      background: cfg.bg,
      color: cfg.color,
      border: `1px solid ${cfg.border}`,
      borderRadius: 4,
      padding: '1px 8px',
      fontSize: 11,
      fontWeight: 700,
      textTransform: 'uppercase' as const,
      letterSpacing: '0.06em',
    }}>
      {cfg.emoji}{cfg.emoji ? ' ' : ''}{cfg.label}
    </span>
  );
};
