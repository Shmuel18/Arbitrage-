/**
 * TelegramContext — lifts the `useTelegramWebApp` hook into a context so
 * that deeply nested components (axios interceptor, keyboard shortcut
 * renderer, compact table views) can all share the same Mini App state
 * without each calling the hook individually.
 *
 * Also side-effects that belong at the app root live here:
 *   * Writing `initData` into a module-level singleton that axios reads
 *     on every request (see services/api.ts).
 *   * Toggling `document.body.classList` to signal Mini App mode to CSS.
 */

import React, { createContext, useContext, useEffect } from 'react';
import { setTelegramInitData } from '../services/api';
import {
  useTelegramWebApp,
  type TelegramContextValue,
} from '../hooks/useTelegramWebApp';

const TelegramContext = createContext<TelegramContextValue | null>(null);

export const TelegramProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const value = useTelegramWebApp();

  // Pipe the signed token into the axios client so every request
  // includes it automatically — no prop-drilling needed.
  useEffect(() => {
    setTelegramInitData(value.initData ?? null);
  }, [value.initData]);

  // Let CSS target Mini App mode (e.g. `.telegram-mini-app .sidebar {…}`).
  useEffect(() => {
    const cls = 'telegram-mini-app';
    if (value.isTelegramWebApp) {
      document.body.classList.add(cls);
    } else {
      document.body.classList.remove(cls);
    }
    return () => document.body.classList.remove(cls);
  }, [value.isTelegramWebApp]);

  return (
    <TelegramContext.Provider value={value}>{children}</TelegramContext.Provider>
  );
};

export function useTelegram(): TelegramContextValue {
  const ctx = useContext(TelegramContext);
  if (!ctx) {
    // Fail soft — if the provider is missing we report "not a Mini App"
    // rather than crashing the whole tree.
    return {
      isTelegramWebApp: false,
      initData: null,
      user: null,
      colorScheme: null,
      webApp: null,
    };
  }
  return ctx;
}
