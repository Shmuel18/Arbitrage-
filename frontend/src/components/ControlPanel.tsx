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
    <div className="card p-5" style={{ position: 'relative' }}>
      {/* Accent top strip */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(139,92,246,0.6), transparent)',
        borderRadius: '14px 14px 0 0',
      }} />

      <div className="card-header mb-4" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.8 }}>
          <circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/>
        </svg>
        {t.controlPanel}
      </div>

      <div className="flex gap-2 mb-3">
        <button onClick={startBot} className="btn btn-success flex-1">
          ▶ {t.startBot}
        </button>
        <button onClick={stopBot} className="btn btn-danger flex-1">
          ■ {t.stopBot}
        </button>
      </div>

      <button onClick={panicStop} className="btn btn-danger w-full mb-4" style={{
        borderColor: 'rgba(239,68,68,0.5)',
        fontWeight: 800,
        letterSpacing: '0.08em',
      }}>
        ⚠ {t.emergencyStop}
      </button>

      <div style={{ borderTop: '1px solid var(--divider)', paddingTop: 16, marginTop: 4 }}>
        <div className="text-xs text-secondary mb-2">{t.strategyToggle}</div>
        <button onClick={toggleStrategy} className="btn btn-outline w-full mono" style={{ fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.05em' }}>
          {t.mode}: <span style={{ color: strategy === 'cherry_pick' ? '#f97316' : '#10b981', marginLeft: 4 }}>{strategy.toUpperCase()}</span>
        </button>
      </div>

      <div style={{ borderTop: '1px solid var(--divider)', paddingTop: 16, marginTop: 16 }}>
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

      {status && (
        <div className="text-xs mt-3 mono" style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--divider)', paddingTop: 8 }}>
          ◈ {status}
        </div>
      )}
    </div>
  );
};

export default ControlPanel;
