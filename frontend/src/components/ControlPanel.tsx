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
    <div className="panel panel-strong p-4">
      <div className="panel-header text-xs mb-3">{t.controlPanel}</div>

      <div className="flex gap-2 mb-3">
        <button onClick={startBot} className="flex-1 px-3 py-2 bg-green-500/15 text-green-300 border border-green-500/30 rounded mono">
          {t.startBot}
        </button>
        <button onClick={stopBot} className="flex-1 px-3 py-2 bg-red-500/15 text-red-300 border border-red-500/30 rounded mono">
          {t.stopBot}
        </button>
      </div>

      <button onClick={panicStop} className="w-full px-3 py-2 mb-3 bg-red-600/25 text-red-200 border border-red-500/40 rounded mono">
        {t.emergencyStop}
      </button>

      <div className="border-t border-slate-800/60 pt-3 mt-3">
        <div className="text-xs text-gray-400 mb-2 mono">{t.strategyToggle}</div>
        <button onClick={toggleStrategy} className="w-full px-3 py-2 bg-cyan-500/10 text-cyan-200 border border-cyan-500/30 rounded mono">
          {t.mode}: {strategy.toUpperCase()}
        </button>
      </div>

      <div className="border-t border-slate-800/60 pt-3 mt-3">
        <div className="text-xs text-gray-400 mb-2 mono">{t.maxConcurrentTrades}</div>
        <div className="flex gap-2">
          <input
            type="number"
            min={1}
            max={10}
            value={maxConcurrent}
            onChange={(e) => setMaxConcurrent(Number(e.target.value))}
            className="flex-1 bg-slate-950 border border-cyan-500/30 rounded px-2 py-1 text-sm text-gray-200 mono"
          />
          <button onClick={applyMaxConcurrent} className="px-3 py-1 bg-cyan-500/20 text-cyan-200 border border-cyan-500/30 rounded mono">
            {t.apply}
          </button>
        </div>
      </div>

      {status && <div className="text-xs text-gray-400 mt-3">{status}</div>}
    </div>
  );
};

export default ControlPanel;
