import React, { useEffect, useState } from 'react';

/**
 * Generic floating holiday banner.
 *
 * Swap the image file at /public/<holiday>.png and the caption text
 * below to reuse for any occasion (Memorial Day, Independence Day, etc).
 *
 * Behavior:
 * - Appears centered near the top of the viewport on every page load.
 * - Can be dismissed via the X button, Escape key, or clicking outside.
 * - Dismissal is NOT persisted — banner reappears on every refresh.
 * - Background remains visible (no dim/blur) so trading operations
 *   stay readable underneath.
 */

// ── Configure the current occasion here ───────────────────────────
const IMAGE_SRC = '/independence-day.png';
const IMAGE_ALT = 'יום העצמאות ה-78 של מדינת ישראל';
const CAPTION = '🇮🇱 חג עצמאות שמח!';
const DISMISS_LABEL = 'סגירת באנר יום העצמאות';
// ──────────────────────────────────────────────────────────────────

export const HolidayBanner: React.FC = () => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Gentle entrance after the dashboard mounts.
    const timer = setTimeout(() => setVisible(true), 400);
    return () => clearTimeout(timer);
  }, []);

  const handleDismiss = () => setVisible(false);

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
      aria-labelledby="holiday-banner-title"
      aria-modal="false"
    >
      <div className="memorial-banner-backdrop" onClick={handleDismiss} />
      <div className="memorial-banner-frame">
        <button
          className="memorial-banner-close"
          onClick={handleDismiss}
          aria-label={DISMISS_LABEL}
          title="סגור"
        >
          ×
        </button>
        <img
          id="holiday-banner-title"
          src={IMAGE_SRC}
          alt={IMAGE_ALT}
          className="memorial-banner-image"
          draggable={false}
        />
        <div className="memorial-banner-caption">{CAPTION}</div>
      </div>
    </div>
  );
};

export default HolidayBanner;
