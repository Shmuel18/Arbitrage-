import React from 'react';

export type TimelineStatus = 'done' | 'live' | 'pending';

export interface TimelineEvent {
  id: string;
  label: string;
  detail: string;
  timeLabel?: string;
  confidence?: number;
  status: TimelineStatus;
}

interface ExecutionTimelineProps {
  title: string;
  events: TimelineEvent[];
}

const ExecutionTimeline: React.FC<ExecutionTimelineProps> = ({ title, events }) => {
  if (events.length === 0) return null;

  return (
    <div className="nx-timeline" aria-label={title}>
      <div className="nx-timeline__title">{title}</div>
      <div className="nx-timeline__list">
        {events.map((event, index) => {
          const isLast = index === events.length - 1;
          return (
            <div key={event.id} className="nx-timeline__item">
              <div className="nx-timeline__rail">
                <span
                  className={`nx-timeline__dot nx-timeline__dot--${event.status}`}
                  aria-hidden="true"
                />
                {!isLast && <span className="nx-timeline__line" aria-hidden="true" />}
              </div>

              <div className="nx-timeline__content">
                <div className="nx-timeline__head">
                  <span className="nx-timeline__label">{event.label}</span>
                  <div className="nx-timeline__meta">
                    {typeof event.confidence === 'number' && (
                      <span
                        className={`nx-timeline__confidence ${
                          event.confidence >= 80
                            ? 'nx-timeline__confidence--high'
                            : event.confidence >= 60
                            ? 'nx-timeline__confidence--mid'
                            : 'nx-timeline__confidence--low'
                        }`}
                      >
                        {event.confidence}%
                      </span>
                    )}
                    {event.timeLabel && (
                      <span className="nx-timeline__time mono">{event.timeLabel}</span>
                    )}
                  </div>
                </div>
                <div className="nx-timeline__detail">{event.detail}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default React.memo(ExecutionTimeline);
