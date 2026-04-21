import React, { useEffect, useState, useCallback } from 'react';
import { SECTION_IDS, SectionId } from './Dashboard';
import { useSettings } from '../context/SettingsContext';

/**
 * Keyboard shortcuts:
 *   ?     open this help
 *   Esc   close help / drawer / modal
 *   g d   go to Dashboard
 *   g p   go to Active Positions
 *   g o   go to Live Opportunities
 *   g t   go to Recent Trades
 *   g b   go to Exchange Portfolio
 *   g l   go to System Log
 *   t     toggle theme
 *
 * Multi-key shortcuts use a 1.2s timeout window (vim-style).
 */

interface KeyboardShortcutsProps {
  onNavigate: (section: SectionId) => void;
  onToggleTheme: () => void;
}

const KeyboardShortcuts: React.FC<KeyboardShortcutsProps> = ({ onNavigate, onToggleTheme }) => {
  const { t, isRtl } = useSettings();
  const [helpOpen, setHelpOpen] = useState(false);

  const SHORTCUTS: Array<{ keys: string; label: string; group: string }> = [
    { keys: '?',   label: t.ksOpenHelp,     group: t.ksGroupGeneral },
    { keys: 'Esc', label: t.ksCloseDialog,  group: t.ksGroupGeneral },
    { keys: 't',   label: t.ksToggleTheme,  group: t.ksGroupGeneral },
    { keys: 'g d', label: t.ksDashboard,    group: t.ksGroupNavigate },
    { keys: 'g p', label: t.ksPositions,    group: t.ksGroupNavigate },
    { keys: 'g o', label: t.ksOpportunities,group: t.ksGroupNavigate },
    { keys: 'g t', label: t.ksTrades,       group: t.ksGroupNavigate },
    { keys: 'g b', label: t.ksBalances,     group: t.ksGroupNavigate },
    { keys: 'g l', label: t.ksLogs,         group: t.ksGroupNavigate },
  ];

  const close = useCallback(() => setHelpOpen(false), []);

  useEffect(() => {
    let gPending = false;
    let gPendingTimer: number | null = null;

    const resetG = () => {
      gPending = false;
      if (gPendingTimer !== null) {
        window.clearTimeout(gPendingTimer);
        gPendingTimer = null;
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      // Ignore when the user is typing in an input/textarea
      const target = e.target as HTMLElement;
      if (target.matches('input, textarea, select, [contenteditable="true"]')) return;
      // Ignore modifier combos (Ctrl/Cmd/Alt) — those are for browser/OS
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      // Handle "g <x>" sequences
      if (gPending) {
        let section: SectionId | null = null;
        switch (e.key) {
          case 'd': section = SECTION_IDS.dashboard; break;
          case 'p': section = SECTION_IDS.positions; break;
          case 'o': section = SECTION_IDS.opportunities; break;
          case 't': section = SECTION_IDS.trades; break;
          case 'b': section = SECTION_IDS.balances; break;
          case 'l': section = SECTION_IDS.logs; break;
        }
        if (section) {
          e.preventDefault();
          onNavigate(section);
        }
        resetG();
        return;
      }

      // Single-key shortcuts
      switch (e.key) {
        case '?':
          e.preventDefault();
          setHelpOpen((o) => !o);
          break;
        case 'Escape':
          if (helpOpen) {
            e.preventDefault();
            setHelpOpen(false);
          }
          break;
        case 't':
          e.preventDefault();
          onToggleTheme();
          break;
        case 'g':
          // Start a "g <x>" sequence
          gPending = true;
          gPendingTimer = window.setTimeout(resetG, 1200);
          break;
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      resetG();
    };
  }, [helpOpen, onNavigate, onToggleTheme]);

  if (!helpOpen) return null;

  // Group shortcuts for display
  const groups = SHORTCUTS.reduce<Record<string, typeof SHORTCUTS>>((acc, s) => {
    (acc[s.group] ||= []).push(s);
    return acc;
  }, {});

  // Split the footer template "Press {key} to open this anywhere · {esc} to close"
  // so we can inject real <kbd> elements without using dangerouslySetInnerHTML.
  const footerTemplate = t.ksFooter;
  const footerParts = footerTemplate.split(/\{key\}|\{esc\}/);
  const footerKeys = [...footerTemplate.matchAll(/\{(key|esc)\}/g)].map((m) => m[1]);

  return (
    <div
      className="nx-shortcut-help"
      role="dialog"
      aria-modal="true"
      aria-label={t.ksTitle}
      dir={isRtl ? 'rtl' : 'ltr'}
    >
      <div className="nx-shortcut-help__backdrop" onClick={close} aria-hidden="true" />
      <div className="nx-shortcut-help__panel">
        <div className="nx-shortcut-help__header">
          <h2>{t.ksTitle}</h2>
          <button
            type="button"
            className="nx-shortcut-help__close"
            onClick={close}
            aria-label={t.ksCloseBtn}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="nx-shortcut-help__body">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="nx-shortcut-help__group">
              <div className="nx-shortcut-help__group-title">{group}</div>
              <ul className="nx-shortcut-help__list">
                {items.map((s) => (
                  <li key={s.keys}>
                    <span className="nx-shortcut-help__label">{s.label}</span>
                    <span className="nx-shortcut-help__keys">
                      {s.keys.split(' ').map((k, i) => (
                        <kbd key={i}>{k}</kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="nx-shortcut-help__footer">
          {footerParts.map((part, i) => (
            <React.Fragment key={i}>
              {part}
              {footerKeys[i] && (
                <kbd>{footerKeys[i] === 'key' ? '?' : 'Esc'}</kbd>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
};

export default KeyboardShortcuts;
