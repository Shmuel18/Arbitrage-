import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { useSettings } from '../context/SettingsContext';
import {
  getBacktestReportHtml,
  getBacktestReports,
  BacktestReportSummary,
} from '../services/api';

// Module-level formatters — cached per the repo's TS conventions
// (never create Intl.NumberFormat in render).
const USD_FMT = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const PCT_FMT = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const SHARPE_FMT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatPnl(value: number | null): string {
  if (value == null || Number.isNaN(value)) return '—';
  const formatted = USD_FMT.format(value);
  return value > 0 ? `+${formatted}` : formatted;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

const BacktestReportsPage: React.FC = () => {
  const { t } = useSettings();
  const [reports, setReports] = useState<BacktestReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [activeName, setActiveName] = useState<string | null>(null);
  const [activeHtml, setActiveHtml] = useState<string | null>(null);
  const [htmlLoading, setHtmlLoading] = useState(false);
  const [htmlError, setHtmlError] = useState<string | null>(null);

  // Fetch the list once on mount; the list only changes when a new report
  // is saved to disk, so no polling is needed.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getBacktestReports(controller.signal)
      .then((res) => {
        setReports(res.reports);
        setListError(null);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        setListError(err instanceof Error ? err.message : 'Failed to load reports');
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const openReport = useCallback(async (name: string) => {
    setActiveName(name);
    setActiveHtml(null);
    setHtmlError(null);
    setHtmlLoading(true);
    try {
      const html = await getBacktestReportHtml(name);
      setActiveHtml(html);
    } catch (err) {
      if (axios.isCancel(err)) return;
      setHtmlError(err instanceof Error ? err.message : 'Failed to load report');
    } finally {
      setHtmlLoading(false);
    }
  }, []);

  const body = useMemo(() => {
    if (loading) {
      return <div className="nx-lazy-fallback" style={{ minHeight: 300 }}><div className="nx-lazy-fallback__shimmer" /></div>;
    }
    if (listError) {
      return <div className="bt-error">⚠ {listError}</div>;
    }
    if (reports.length === 0) {
      return (
        <div className="bt-empty">
          <p>{t.noReportsYet}</p>
          <pre className="bt-hint">{t.backtestRunHint}</pre>
        </div>
      );
    }
    return (
      <div className="bt-list">
        <table className="bt-table">
          <thead>
            <tr>
              <th>{t.backtestReports}</th>
              <th>Symbol</th>
              <th>Pair</th>
              <th>Trades</th>
              <th>{t.winRate}</th>
              <th>{t.netPnl}</th>
              <th>Sharpe</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr key={r.name} className={activeName === r.name ? 'bt-row active' : 'bt-row'}>
                <td className="bt-name" title={r.name}>{r.name}</td>
                <td>{r.symbol ?? '—'}</td>
                <td>{r.exchange_a && r.exchange_b ? `${r.exchange_a} ↔ ${r.exchange_b}` : '—'}</td>
                <td>{r.trade_count ?? '—'}</td>
                <td>{r.win_rate != null ? PCT_FMT.format(r.win_rate) : '—'}</td>
                <td className={r.total_pnl_usd != null && r.total_pnl_usd < 0 ? 'bt-pnl-neg' : 'bt-pnl-pos'}>
                  {formatPnl(r.total_pnl_usd)}
                </td>
                <td>{r.sharpe_ratio_annualized != null ? SHARPE_FMT.format(r.sharpe_ratio_annualized) : '—'}</td>
                <td>{formatDate(r.created_at)}</td>
                <td>
                  <button type="button" className="bt-view-btn" onClick={() => openReport(r.name)}>
                    {t.viewReport}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }, [loading, listError, reports, activeName, openReport, t]);

  return (
    <div className="bt-page">
      <h2 className="bt-page-title">{t.backtestReports}</h2>

      {body}

      {activeName && (
        <div className="bt-viewer">
          <div className="bt-viewer-header">
            <span className="bt-viewer-name">{activeName}</span>
            <button type="button" className="bt-close-btn" onClick={() => { setActiveName(null); setActiveHtml(null); }}>
              ×
            </button>
          </div>
          {htmlLoading && <div className="nx-lazy-fallback" style={{ minHeight: 400 }}><div className="nx-lazy-fallback__shimmer" /></div>}
          {htmlError && <div className="bt-error">⚠ {htmlError}</div>}
          {activeHtml && !htmlLoading && !htmlError && (
            <iframe
              className="bt-iframe"
              title={activeName}
              srcDoc={activeHtml}
              sandbox="allow-scripts"
            />
          )}
        </div>
      )}
    </div>
  );
};

export default BacktestReportsPage;
