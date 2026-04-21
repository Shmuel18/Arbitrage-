import React, { useEffect, useState } from 'react';

/**
 * Respectful floating banner for Yom HaZikaron (Israel Memorial Day).
 *
 * Behavior:
 * - Appears centered near the top of the viewport on first render.
 * - Can be dismissed via the X button; dismissal is remembered for the
 *   current calendar date (so the banner returns next time).
 * - Does not block trading operations — it floats above content but a
 *   dismiss button is always visible.
 *
 * The candle image lives at /public/memorial-day.jpg.
 */
const STORAGE_KEY = 'ratebridge_memorial_banner_dismissed';

function isDismissedToday(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return false;
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
    return stored === today;
  } catch {
    return false;
  }
}

function markDismissedToday(): void {
  try {
    const today = new Date().toISOString().slice(0, 10);
    localStorage.setItem(STORAGE_KEY, today);
  } catch {
    /* ignore storage errors */
  }
}

export const MemorialDayBanner: React.FC = () => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Delay slightly for a gentler entrance after the dashboard mounts.
    const timer = setTimeout(() => {
      if (!isDismissedToday()) {
        setVisible(true);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, []);

  const handleDismiss = () => {
    setVisible(false);
    markDismissedToday();
  };

  // Keyboard: Escape closes the banner
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleDismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [visible]);

  if (!visible) return null;

  return (
    <div
      className="memorial-banner"
      role="dialog"
      aria-labelledby="memorial-banner-title"
      aria-modal="false"
    >
      <div className="memorial-banner-backdrop" onClick={handleDismiss} />
      <div className="memorial-banner-frame">
        <button
          className="memorial-banner-close"
          onClick={handleDismiss}
          aria-label="סגירת באנר יום הזיכרון"
          title="סגור"
        >
          ×
        </button>
        <img
          id="memorial-banner-title"
          src="/memorial-day.jpg"
          alt="יום הזיכרון לחללי מערכות ישראל"
          className="memorial-banner-image"
          draggable={false}
        />
        <div className="memorial-banner-caption">
          יהי זכרם ברוך
        </div>
      </div>
    </div>
  );
};

export default MemorialDayBanner;
