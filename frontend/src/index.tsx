import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { SettingsProvider } from './context/SettingsContext';
import { ToastProvider } from './context/ToastContext';
import { TelegramProvider } from './context/TelegramContext';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <SettingsProvider>
      <TelegramProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </TelegramProvider>
    </SettingsProvider>
  </React.StrictMode>
);
