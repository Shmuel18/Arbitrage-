import React, { useCallback, useState } from 'react';
import axios from 'axios';
import { useSettings } from '../context/SettingsContext';
import { startBacktestRun, RunJobParams } from '../services/api';
import { useBacktestJob } from '../hooks/useBacktestJob';
import BacktestJobStatus from './BacktestJobStatus';

const EXCHANGES = ['binance', 'bybit', 'kucoin', 'gateio', 'bitget'] as const;

const BacktestRunForm: React.FC = () => {
  const { t } = useSettings();

  const [symbol, setSymbol] = useState<string>('BTC/USDT:USDT');
  const [exchangeA, setExchangeA] = useState<string>('binance');
  const [exchangeB, setExchangeB] = useState<string>('bybit');
  const [notional, setNotional] = useState<number>(100);
  const [minSpread, setMinSpread] = useState<number>(0.003);
  const [maxHoldHours, setMaxHoldHours] = useState<number>(72);
  const [maxCollections, setMaxCollections] = useState<number>(6);
  const [slippageBps, setSlippageBps] = useState<number>(5);
  const [fundingInterval, setFundingInterval] = useState<number>(8);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, error: pollError } = useBacktestJob(jobId);

  const submit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const params: RunJobParams = {
        symbol: symbol.toUpperCase(),
        exchange_a: exchangeA,
        exchange_b: exchangeB,
        notional_usd: notional,
        min_funding_spread: minSpread,
        max_hold_hours: maxHoldHours,
        max_collections: maxCollections,
        slippage_bps: slippageBps,
        funding_interval_hours: fundingInterval,
      };
      const res = await startBacktestRun(params);
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
  }, [symbol, exchangeA, exchangeB, notional, minSpread, maxHoldHours, maxCollections, slippageBps, fundingInterval]);

  return (
    <div className="bt-form-page">
      <h3 className="bt-form-title">{t.backtestRunTitle}</h3>

      <form className="bt-form bt-form-wide" onSubmit={submit}>
        <label className="bt-field bt-field-wide">
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
          <span>{t.backtestExchangeA}</span>
          <select value={exchangeA} onChange={(e) => setExchangeA(e.target.value)}>
            {EXCHANGES.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </label>

        <label className="bt-field">
          <span>{t.backtestExchangeB}</span>
          <select value={exchangeB} onChange={(e) => setExchangeB(e.target.value)}>
            {EXCHANGES.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </label>

        <label className="bt-field">
          <span>{t.backtestNotional}</span>
          <input type="number" min={1} step="0.01" value={notional}
                 onChange={(e) => setNotional(Number(e.target.value))} />
        </label>

        <label className="bt-field">
          <span>{t.backtestMinSpread}</span>
          <input type="number" min={0} step="0.0001" value={minSpread}
                 onChange={(e) => setMinSpread(Number(e.target.value))} />
        </label>

        <label className="bt-field">
          <span>{t.backtestMaxHold}</span>
          <input type="number" min={1} step="1" value={maxHoldHours}
                 onChange={(e) => setMaxHoldHours(Number(e.target.value))} />
        </label>

        <label className="bt-field">
          <span>{t.backtestMaxCollections}</span>
          <input type="number" min={1} step="1" value={maxCollections}
                 onChange={(e) => setMaxCollections(Number(e.target.value))} />
        </label>

        <label className="bt-field">
          <span>{t.backtestSlippage}</span>
          <input type="number" min={0} step="0.5" value={slippageBps}
                 onChange={(e) => setSlippageBps(Number(e.target.value))} />
        </label>

        <label className="bt-field">
          <span>{t.backtestFundingInterval}</span>
          <input type="number" min={1} step="1" value={fundingInterval}
                 onChange={(e) => setFundingInterval(Number(e.target.value))} />
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

export default BacktestRunForm;
