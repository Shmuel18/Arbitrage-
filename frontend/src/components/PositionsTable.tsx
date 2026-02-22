import React from 'react';
import { useSettings } from '../context/SettingsContext';

interface PositionRow {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_qty: string;
  short_qty: string;
  entry_edge_pct: string;
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  immediate_spread_pct?: string | null;
  current_spread_pct?: string | null;
  current_long_rate?: string | null;
  current_short_rate?: string | null;
  entry_price_long?: string | null;
  next_funding_ms?: number | null;
  mode?: string;
  state: string;
}

interface PositionsTableProps {
  positions: PositionRow[];
}

const PositionsTable: React.FC<PositionsTableProps> = ({ positions }) => {
  const { t } = useSettings();

  const formatFunding = (rate?: string | null) => {
    if (!rate) return '--';
    const n = Number(rate);
    if (Number.isNaN(n)) return '--';
    const pct = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(3)}%`;
  };

  const formatSpread = (val?: string | null) => {
    if (!val) return '--';
    const n = Number(val);
    if (Number.isNaN(n)) return '--';
    return `${n.toFixed(2)}%`;
  };

  const formatCountdown = (ms?: number | null): string => {
    if (!ms) return '--';
    const diff = ms - Date.now();
    if (diff <= 0) return '⚡ NOW';
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m`;
    return `${Math.floor(mins / 60)}h${mins % 60 > 0 ? (mins % 60) + 'm' : ''}`;
  };

  const countdownStyle = (ms?: number | null): React.CSSProperties => {
    if (!ms) return {};
    const diff = ms - Date.now();
    if (diff <= 0) return { color: '#10b981', fontWeight: 700 };
    const mins = diff / 60000;
    if (mins < 2) return { color: '#ef4444', fontWeight: 700, animation: 'funding-blink 0.8s ease-in-out infinite' };
    if (mins < 5) return { color: '#f97316', fontWeight: 600 };
    if (mins < 15) return { color: '#eab308', fontWeight: 500 };
    return { color: '#10b981' };
  };

  const modeLabel = (mode?: string) => {
    if (!mode) return null;
    const m = mode.toLowerCase();
    if (m === 'cherry_pick') return (
      <span className="pos-mode-badge pos-mode-badge--cherry">🍒 CHERRY</span>
    );
    if (m === 'hold_mixed') return (
      <span className="pos-mode-badge pos-mode-badge--mixed">MIXED</span>
    );
    return <span className="pos-mode-badge pos-mode-badge--hold">HOLD</span>;
  };

  return (
    <div className="card" style={{ position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent)',
        borderRadius: '14px 14px 0 0',
        zIndex: 1, pointerEvents: 'none',
      }} />
      <div className="card-header px-5 py-3 border-b" style={{ borderColor: 'var(--card-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.8 }}>
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
        </svg>
        {t.activePositions}
        {positions.length > 0 && (
          <span className="xcard-live" style={{ marginLeft: 2 }}>
            <span className="xcard-live-dot" />LIVE
          </span>
        )}
        {positions.length > 0 && (
          <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
            {positions.length} position{positions.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>
      <div className="overflow-x-auto scrollbar-thin">
        <table className="corp-table" style={{ fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ lineHeight: '1.2' }}>
              <th style={{ padding: '6px 8px', textAlign: 'left' }}>{t.symbol}</th>
              <th style={{ padding: '6px 8px', textAlign: 'left' }}>Ex</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>{t.qtyLS}</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>{t.sizeUsd}</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Entry%</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Immed</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>8h</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Fund%</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>{t.nextPayout}</th>
              <th style={{ padding: '6px 8px', textAlign: 'center' }}>{t.state}</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={10} className="text-center text-secondary py-4" style={{ fontSize: '0.85rem' }}>
                  {t.noOpenPositions}
                </td>
              </tr>
            ) : (
              positions.map((p) => {
                const entryVal = Number(p.entry_edge_pct || 0);
                const immediateVal = Number(p.immediate_spread_pct || 0);
                const currentVal = Number(p.current_spread_pct || 0);
                
                const spreadDiff = currentVal - entryVal;
                const spreadColor = !p.current_spread_pct ? 'text-secondary'
                  : spreadDiff > 0 ? 'text-green-400'
                  : spreadDiff < -0.1 ? 'text-red-400'
                  : 'text-yellow-400';
                const arrow = !p.current_spread_pct ? ''
                  : spreadDiff > 0 ? ' ▲' : spreadDiff < 0 ? ' ▼' : ' =';
                
                const immediateColor = !p.immediate_spread_pct ? 'text-secondary'
                  : immediateVal > 0 ? 'text-green-400'
                  : immediateVal < -0.1 ? 'text-red-400'
                  : 'text-yellow-400';
                  
                return (
                  <tr key={p.id} className="pos-row--active" style={{ lineHeight: '1.2' }}>
                    <td style={{ padding: '5px 8px', fontWeight: 500 }} className="text-accent">
                      <div>{p.symbol}</div>
                      <div style={{ marginTop: 1 }}>{modeLabel(p.mode)}</div>
                    </td>
                    <td style={{ padding: '5px 8px', fontSize: '0.75rem' }} className="text-secondary">
                      {p.long_exchange?.slice(0,2).toUpperCase()}/{p.short_exchange?.slice(0,2).toUpperCase()}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontSize: '0.8rem' }} className="mono">
                      {parseFloat(p.long_qty).toPrecision(6)}/{parseFloat(p.short_qty).toPrecision(6)}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontSize: '0.8rem' }} className="mono">
                      {p.entry_price_long
                        ? '$' + (parseFloat(p.long_qty) * parseFloat(p.entry_price_long)).toLocaleString('en-US', { maximumFractionDigits: 0 })
                        : '--'}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontSize: '0.8rem' }} className="mono">
                      {formatSpread(p.entry_edge_pct)}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 500 }} className={`mono ${immediateColor}`}>
                      {formatSpread(p.immediate_spread_pct)}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 500 }} className={`mono ${spreadColor}`}>
                      {formatSpread(p.current_spread_pct)}{arrow}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontSize: '0.8rem' }} className="mono">
                      {formatFunding(p.current_long_rate)}/{formatFunding(p.current_short_rate)}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontSize: '0.8rem', ...countdownStyle(p.next_funding_ms) }} className="mono">
                      {formatCountdown(p.next_funding_ms)}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'center', fontSize: '0.8rem' }} className="text-secondary">
                      {p.state}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default PositionsTable;
