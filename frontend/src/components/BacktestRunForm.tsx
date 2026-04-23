import React from 'react';
import { useSettings } from '../context/SettingsContext';

/** Placeholder — real form + backend wiring lands in the next commit. */
const BacktestRunForm: React.FC = () => {
  const { t } = useSettings();
  return (
    <div className="bt-placeholder">
      <h3>{t.backtestRunTitle}</h3>
      <p>{t.backtestComingSoon}</p>
    </div>
  );
};

export default BacktestRunForm;
