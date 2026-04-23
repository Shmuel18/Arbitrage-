import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { BacktestJob, getBacktestJob } from '../services/api';

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set<BacktestJob['status']>(['succeeded', 'failed']);

/**
 * Poll a backtest job every 2 s until it reaches a terminal state.
 * Pass ``null`` to reset/stop polling. Cleans up on unmount and on job change.
 */
export function useBacktestJob(jobId: string | null): {
  job: BacktestJob | null;
  error: string | null;
  isDone: boolean;
} {
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setError(null);
      return undefined;
    }

    setJob(null);
    setError(null);
    let cancelled = false;
    const controller = new AbortController();
    controllerRef.current = controller;

    const tick = async (): Promise<void> => {
      try {
        const data = await getBacktestJob(jobId, controller.signal);
        if (cancelled) return;
        setJob(data);
        if (!TERMINAL_STATUSES.has(data.status)) {
          setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled || axios.isCancel(err)) return;
        setError(err instanceof Error ? err.message : 'Polling failed');
        // Retry once after a short delay — transient Redis hiccups happen.
        setTimeout(tick, POLL_INTERVAL_MS * 2);
      }
    };
    tick();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId]);

  const isDone = job != null && TERMINAL_STATUSES.has(job.status);
  return { job, error, isDone };
}
