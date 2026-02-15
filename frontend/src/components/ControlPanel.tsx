import React, { useState } from 'react';
import { sendBotCommand, updateConfig, emergencyStop } from '../services/api';
import { useSettings } from '../context/SettingsContext';

const ControlPanel: React.FC = () => {
  const { t } = useSettings();
  const [maxConcurrent, setMaxConcurrent] = useState(3);
  const [strategy, setStrategy] = useState<'hold' | 'cherry_pick'>('hold');
  const [status, setStatus] = useState('');

  const applyMaxConcurrent = async () => {
    try {
      await updateConfig('execution.concurrent_opportunities', maxConcurrent);
      setStatus(t.settingsUpdated);
    } catch (e) {
      setStatus(t.settingsFailed);
    }
  };

  const toggleStrategy = async () => {
    const next = strategy === 'hold' ? 'cherry_pick' : 'hold';
    setStrategy(next);
    try {
      await updateConfig('trading_params.strategy_mode', next);
      setStatus(t.strategySet);
    } catch (e) {
      setStatus(t.strategyFailed);
    }
  };

  const startBot = async () => {
    await sendBotCommand('start');
    setStatus(t.startSent);
  };

  const stopBot = async () => {
    await sendBotCommand('stop');
    setStatus(t.stopSent);
  };

  const panicStop = async () => {
    await emergencyStop();
    setStatus(t.emergencySent);
  };

  return (
    <div className="card p-5">
      <div className="card-header mb-4">{t.controlPanel}</div>

      <div className="flex gap-2 mb-3">
        <button onClick={startBot} className="btn btn-success flex-1">
          {t.startBot}
        </button>
        <button onClick={stopBot} className="btn btn-danger flex-1">
          {t.stopBot}
        </button>
      </div>

      <button onClick={panicStop} className="btn btn-danger w-full mb-4">
        {t.emergencyStop}
      </button>

      <div className="section-divider pt-4 mt-1">
        <div className="text-xs text-secondary mb-2">{t.strategyToggle}</div>
        <button onClick={toggleStrategy} className="btn btn-outline w-full mono">
          {t.mode}: {strategy.toUpperCase()}
        </button>
      </div>

      <div className="section-divider pt-4 mt-4">
        <div className="text-xs text-secondary mb-2">{t.maxConcurrentTrades}</div>
        <div className="flex gap-2">
          <input
            type="number"
            min={1}
            max={10}
            value={maxConcurrent}
            onChange={(e) => setMaxConcurrent(Number(e.target.value))}
            className="input flex-1"
          />
          <button onClick={applyMaxConcurrent} className="btn btn-primary">
            {t.apply}
          </button>
        </div>
      </div>

      {status && <div className="text-xs text-secondary mt-3">{status}</div>}
    </div>
  );
};

export default ControlPanel;
