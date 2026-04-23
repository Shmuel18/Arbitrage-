import React from 'react';
import { useSettings } from '../context/SettingsContext';
import { BacktestJob } from '../services/api';

interface Props {
  job: BacktestJob | null;
  pollError: string | null;
}

function statusLabel(
  status: BacktestJob['status'] | undefined,
  t: { backtestRunning: string; backtestJobSucceeded: string; backtestJobFailed: string },
): string {
  if (!status) return '—';
  switch (status) {
    case 'queued':    return 'queued…';
    case 'running':   return t.backtestRunning;
    case 'succeeded': return t.backtestJobSucceeded;
    case 'failed':    return t.backtestJobFailed;
    default:          return status;
  }
}

const BacktestJobStatus: React.FC<Props> = ({ job, pollError }) => {
  const { t } = useSettings();
  if (!job && !pollError) return null;

  const status = job?.status ?? 'failed';
  const cls =
    status === 'succeeded' ? 'bt-job-success'
    : status === 'failed' ? 'bt-job-fail'
    : 'bt-job-running';

  return (
    <div className={`bt-job ${cls}`}>
      <div className="bt-job-head">
        <span className="bt-job-badge">{statusLabel(status, t)}</span>
        {job?.id && <span className="bt-job-id">job {job.id}</span>}
        {job?.report_name && (
          <span className="bt-job-report-name">→ {job.report_name}</span>
        )}
      </div>

      {pollError && <div className="bt-error">⚠ {pollError}</div>}

      {job?.error && <div className="bt-error">⚠ {job.error}</div>}

      {(job?.stdout_tail || job?.stderr_tail) && (
        <details className="bt-job-logs" open={status === 'failed'}>
          <summary>logs</summary>
          {job.stdout_tail && (
            <>
              <div className="bt-job-log-label">stdout</div>
              <pre className="bt-job-log">{job.stdout_tail}</pre>
            </>
          )}
          {job.stderr_tail && (
            <>
              <div className="bt-job-log-label">stderr</div>
              <pre className="bt-job-log">{job.stderr_tail}</pre>
            </>
          )}
        </details>
      )}
    </div>
  );
};

export default BacktestJobStatus;
