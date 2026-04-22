/**
 * Floating AI chat widget — bottom-right corner of the dashboard.
 *
 * Click the bubble to open a side panel with a chat input. Each message
 * is sent to POST /api/ai/chat and the AI's HTML answer is rendered
 * (safe subset: <b>, <i>, <code>).
 *
 * The widget is intentionally minimal — no history persistence, no
 * multi-turn context. Each question is a fresh call to the AI assistant.
 */

import React, { useEffect, useRef, useState, KeyboardEvent } from 'react';
import { useSettings } from '../context/SettingsContext';
import api from '../services/api';

interface ChatMessage {
  role: 'user' | 'ai';
  text: string;
  ts: number;
  error?: boolean;
}

/** Ultra-minimal HTML sanitizer: only allow <b> <i> <code>, escape everything else. */
function sanitize(html: string): string {
  // First escape all HTML
  const escaped = html
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // Then selectively re-allow our safe tags
  return escaped
    .replace(/&lt;b&gt;/g, '<b>')
    .replace(/&lt;\/b&gt;/g, '</b>')
    .replace(/&lt;i&gt;/g, '<i>')
    .replace(/&lt;\/i&gt;/g, '</i>')
    .replace(/&lt;code&gt;/g, '<code>')
    .replace(/&lt;\/code&gt;/g, '</code>')
    .replace(/\n/g, '<br/>');
}

const QUICK_PROMPTS_HE = [
  'כמה הרווחתי היום?',
  'מה הפוזיציות הפתוחות?',
  'מה ההזדמנויות הכי טובות?',
  'למה הבוט לא סוחר?',
];

const QUICK_PROMPTS_EN = [
  "How much did I make today?",
  'What positions are open?',
  "What are the best opportunities now?",
  "Why isn't the bot trading?",
];

export const AIChatWidget: React.FC = () => {
  const { lang, isRtl } = useSettings();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const quickPrompts = lang === 'he' ? QUICK_PROMPTS_HE : QUICK_PROMPTS_EN;
  const strings = lang === 'he'
    ? {
        title: '🤖 AI Assistant',
        placeholder: 'שאל שאלה על הבוט...',
        send: 'שלח',
        thinking: 'חושב...',
        welcome: 'היי! שאל אותי כל שאלה על הבוט שלך.',
      }
    : {
        title: '🤖 AI Assistant',
        placeholder: 'Ask about the bot...',
        send: 'Send',
        thinking: 'Thinking...',
        welcome: 'Hi! Ask me anything about your bot.',
      };

  // Auto-scroll to newest message
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, busy]);

  // Focus input on open
  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', text: question, ts: Date.now() }]);
    setBusy(true);
    try {
      const resp = await api.post('/ai/chat', { question, lang });
      const answer: string = resp.data?.answer || '(empty)';
      setMessages((prev) => [...prev, { role: 'ai', text: answer, ts: Date.now() }]);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Request failed';
      setMessages((prev) => [
        ...prev,
        { role: 'ai', text: detail, ts: Date.now(), error: true },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <>
      {/* Floating bubble */}
      {!open && (
        <button
          className="ai-chat-fab"
          onClick={() => setOpen(true)}
          aria-label={strings.title}
          title={strings.title}
        >
          <span className="ai-chat-fab-icon">🤖</span>
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className={`ai-chat-panel ${isRtl ? 'ai-chat-rtl' : 'ai-chat-ltr'}`}>
          <header className="ai-chat-header">
            <span className="ai-chat-title">{strings.title}</span>
            <button
              className="ai-chat-close"
              onClick={() => setOpen(false)}
              aria-label="Close"
            >
              ×
            </button>
          </header>

          <div className="ai-chat-body" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="ai-chat-welcome">
                <p>{strings.welcome}</p>
                <div className="ai-chat-quick">
                  {quickPrompts.map((q) => (
                    <button
                      key={q}
                      className="ai-chat-quick-btn"
                      onClick={() => send(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div
                key={i}
                className={`ai-chat-msg ai-chat-msg-${m.role} ${m.error ? 'ai-chat-msg-error' : ''}`}
              >
                {m.role === 'ai' ? (
                  <div
                    className="ai-chat-msg-body"
                    dangerouslySetInnerHTML={{ __html: sanitize(m.text) }}
                  />
                ) : (
                  <div className="ai-chat-msg-body">{m.text}</div>
                )}
              </div>
            ))}

            {busy && (
              <div className="ai-chat-msg ai-chat-msg-ai ai-chat-typing">
                <span>{strings.thinking}</span>
                <span className="ai-chat-dots">
                  <span /> <span /> <span />
                </span>
              </div>
            )}
          </div>

          <div className="ai-chat-input-row">
            <textarea
              ref={inputRef}
              className="ai-chat-input"
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder={strings.placeholder}
              disabled={busy}
            />
            <button
              className="ai-chat-send"
              onClick={() => send(input)}
              disabled={busy || !input.trim()}
            >
              {strings.send}
            </button>
          </div>
        </div>
      )}
    </>
  );
};

export default AIChatWidget;
