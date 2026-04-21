import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

/* ── Types ─────────────────────────────────────────────────────── */
export type ToastIntent = 'info' | 'success' | 'warning' | 'error';

export interface Toast {
  id: string;
  intent: ToastIntent;
  title?: string;
  message: string;
  duration?: number; // ms; 0 = sticky
}

interface ToastContextValue {
  push: (toast: Omit<Toast, 'id'>) => string;
  dismiss: (id: string) => void;
  dismissAll: () => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}

/* ── Provider ──────────────────────────────────────────────────── */
export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback<ToastContextValue['push']>(
    (toast) => {
      const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const duration = toast.duration ?? 4500;
      setToasts((prev) => [...prev, { ...toast, id }]);
      if (duration > 0) {
        const timer = window.setTimeout(() => dismiss(id), duration);
        timers.current.set(id, timer);
      }
      return id;
    },
    [dismiss]
  );

  const dismissAll = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current.clear();
    setToasts([]);
  }, []);

  // Cleanup on unmount
  useEffect(() => () => dismissAll(), [dismissAll]);

  return (
    <ToastContext.Provider value={{ push, dismiss, dismissAll }}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
};

/* ── Viewport ──────────────────────────────────────────────────── */
const INTENT_ICON: Record<ToastIntent, React.ReactNode> = {
  info: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  ),
  success: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  ),
  warning: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  ),
  error: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
    </svg>
  ),
};

const ToastViewport: React.FC<{ toasts: Toast[]; onDismiss: (id: string) => void }> = ({ toasts, onDismiss }) => {
  if (toasts.length === 0) return null;
  return (
    <div
      className="nx-toast-viewport"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`nx-toast nx-toast--${t.intent}`}
          role={t.intent === 'error' || t.intent === 'warning' ? 'alert' : 'status'}
        >
          <div className="nx-toast__icon" aria-hidden="true">
            {INTENT_ICON[t.intent]}
          </div>
          <div className="nx-toast__body">
            {t.title && <div className="nx-toast__title">{t.title}</div>}
            <div className="nx-toast__message">{t.message}</div>
          </div>
          <button
            type="button"
            className="nx-toast__close"
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss notification"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
};
