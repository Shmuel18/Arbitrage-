import { useEffect, useState } from 'react';

/**
 * 1-second tick hook for live time-dependent re-renders.
 *
 * Returns the current Date.now() and refreshes every `intervalMs`.
 * Use it inside any component that needs to show "live" time-derived
 * values (countdowns, elapsed durations, time-window badges) without
 * waiting for the next websocket / poll push to trigger a re-render.
 *
 * Prefer 1000 ms (default) — ticking faster than that just burns CPU
 * since visible time-displays don't refresh sub-second.
 */
export const useNow = (intervalMs: number = 1000): number => {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
};
