/**
 * useTelegramWebApp — detect and interface with Telegram's Mini App SDK.
 *
 * The SDK is loaded via <script> in index.html. When the dashboard is opened
 * inside a regular browser the global `window.Telegram` is either undefined
 * or `.WebApp.initData` is empty — we treat that as "not a Mini App".
 *
 * This hook:
 *   1. Detects Mini App context
 *   2. Calls `ready()` so Telegram renders the app
 *   3. Calls `expand()` so the web view uses the full viewport (not the
 *      small compact height)
 *   4. Exposes `initData`, `colorScheme`, `user`, and viewport metrics to
 *      the rest of the app through a stable reference.
 *   5. Subscribes to theme changes so React components re-render when the
 *      user toggles Telegram's dark/light mode.
 *
 * Intentionally kept plain-TypeScript — no typings for the SDK are needed
 * because we narrow access through small helpers (`getWebApp`).
 */

import { useEffect, useState } from 'react';

// Minimal shape of the bits we actually use. The real surface is larger;
// we deliberately don't import `@types/telegram-web-app` to avoid the dep.
interface TelegramThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
}
interface TelegramWebAppUser {
  id: number;
  is_bot?: boolean;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}
interface TelegramWebApp {
  initData: string;
  colorScheme: 'light' | 'dark';
  themeParams: TelegramThemeParams;
  viewportHeight: number;
  viewportStableHeight: number;
  isExpanded: boolean;
  initDataUnsafe?: { user?: TelegramWebAppUser };
  ready: () => void;
  expand: () => void;
  close: () => void;
  onEvent: (evt: string, cb: () => void) => void;
  offEvent: (evt: string, cb: () => void) => void;
  enableClosingConfirmation?: () => void;
  HapticFeedback?: {
    impactOccurred: (style: 'light' | 'medium' | 'heavy') => void;
    notificationOccurred: (type: 'error' | 'success' | 'warning') => void;
  };
}

function getWebApp(): TelegramWebApp | null {
  if (typeof window === 'undefined') return null;
  const tg = (window as unknown as { Telegram?: { WebApp?: TelegramWebApp } }).Telegram;
  return tg?.WebApp ?? null;
}

export interface TelegramContextValue {
  /** True when the page is actually running inside a Telegram Mini App. */
  isTelegramWebApp: boolean;
  /** Signed initData querystring — pass as X-Telegram-Init-Data header. */
  initData: string | null;
  /** Decoded user object, convenient accessor. */
  user: TelegramWebAppUser | null;
  /** Telegram's active color scheme (overrides user's stored preference). */
  colorScheme: 'light' | 'dark' | null;
  /** Raw SDK handle for rare use cases (haptic feedback, close, etc). */
  webApp: TelegramWebApp | null;
}

const EMPTY: TelegramContextValue = {
  isTelegramWebApp: false,
  initData: null,
  user: null,
  colorScheme: null,
  webApp: null,
};

export function useTelegramWebApp(): TelegramContextValue {
  const [state, setState] = useState<TelegramContextValue>(() => {
    const wa = getWebApp();
    // `initData` is empty string when the page is opened outside Telegram,
    // even if the SDK loaded. That's our real detection signal.
    if (!wa || !wa.initData) return EMPTY;
    return {
      isTelegramWebApp: true,
      initData: wa.initData,
      user: wa.initDataUnsafe?.user ?? null,
      colorScheme: wa.colorScheme,
      webApp: wa,
    };
  });

  useEffect(() => {
    const wa = getWebApp();
    if (!wa || !wa.initData) return;

    // 1. Tell Telegram we're ready so it renders the app.
    try { wa.ready(); } catch { /* no-op */ }
    // 2. Take the full viewport height (not the default collapsed 50%).
    try { wa.expand(); } catch { /* no-op */ }

    // 3. Subscribe to theme changes so React re-renders when the user
    //    flips Telegram's dark/light toggle.
    const onThemeChange = () => {
      const w = getWebApp();
      if (!w) return;
      setState((prev) => ({ ...prev, colorScheme: w.colorScheme }));
      // Also reflect into <html data-theme> immediately (some CSS only
      // reacts to the attribute, not React state).
      document.documentElement.setAttribute('data-theme', w.colorScheme);
    };
    try { wa.onEvent('themeChanged', onThemeChange); } catch { /* no-op */ }

    return () => {
      try { wa.offEvent('themeChanged', onThemeChange); } catch { /* no-op */ }
    };
  }, []);

  return state;
}
