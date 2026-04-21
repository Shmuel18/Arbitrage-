import React, { useEffect, useState } from 'react';

/**
 * Respectful floating banner for Yom HaZikaron (Israel Memorial Day).
 *
 * Behavior:
 * - Appears centered near the top of the viewport on every page load.
 * - Can be dismissed via the X button, Escape key, or clicking outside.
 * - Dismissal is NOT persisted — banner reappears on every refresh.
 * - Background remains visible (no dim/blur) so trading operations
 *   stay readable underneath.
 *
 * The candle image lives at /public/memorial-day.jpg.
 */
export const MemorialDayBanner: React.FC = () => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Delay slightly for a gentler entrance after the dashboard mounts.
    const timer = setTimeout(() => setVisible(true), 400);
    return () => clearTimeout(timer);
  }, []);

  const handleDismiss = () => setVisible(false);

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
