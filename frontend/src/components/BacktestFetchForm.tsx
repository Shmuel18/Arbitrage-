import React, { useCallback, useState } from 'react';
import axios from 'axios';
import { useSettings } from '../context/SettingsContext';
import { startBacktestFetch, FetchJobParams } from '../services/api';
import { useBacktestJob } from '../hooks/useBacktestJob';
import BacktestJobStatus from './BacktestJobStatus';

const EXCHANGES = ['binance', 'bybit', 'kucoin', 'gateio', 'bitget'] as const;
const KINDS: FetchJobParams['kind'][] = ['both', 'funding', 'ohlcv-1d'];

const BacktestFetchForm: React.FC = () => {
  const { t } = useSettings();
  const [exchange, setExchange] = useState<string>('binance');
  const [symbol, setSymbol] = useState<string>('BTC/USDT:USDT');
  const [days, setDays] = useState<number>(30);
  const [kind, setKind] = useState<FetchJobParams['kind']>('both');

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, error: pollError } = useBacktestJob(jobId);

  const submit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await startBacktestFetch({ exchange, symbol: symbol.toUpperCase(), days, kind });
      setJobId(res.job_id);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.data?.detail) {
        setSubmitError(JSON.stringify(err.response.data.detail));
      } else {
        setSubmitError(err instanceof Error ? err.message : 'Submit failed');
      }
    } finally {
      setSubmitting(false);
    }
  }, [exchange, symbol, days, kind]);

  return (
    <div className="bt-form-page">
      <h3 className="bt-form-title">{t.backtestFetchTitle}</h3>

      <form className="bt-form" onSubmit={submit}>
        <label className="bt-field">
          <span>{t.backtestExchange}</span>
          <select value={exchange} onChange={(e) => setExchange(e.target.value)}>
            {EXCHANGES.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </label>

        <label className="bt-field">
          <span>{t.backtestSymbol}</span>
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="BTC/USDT:USDT"
            spellCheck={false}
          />
        </label>

        <label className="bt-field">
          <span>{t.backtestDays}</span>
          <input
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </label>

        <label className="bt-field">
          <span>{t.backtestKind}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as FetchJobParams['kind'])}>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>

        <div className="bt-form-actions">
          <button type="submit" className="bt-primary-btn" disabled={submitting}>
            {submitting ? '…' : t.backtestSubmit}
          </button>
        </div>
      </form>

      {submitError && <div className="bt-error bt-form-error">⚠ {submitError}</div>}
      <BacktestJobStatus job={job} pollError={pollError} />
    </div>
  );
};

export default BacktestFetchForm;
