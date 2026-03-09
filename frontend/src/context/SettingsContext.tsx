import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { Lang, Translations, translations } from '../i18n/translations';

export type Theme = 'dark' | 'light';

interface SettingsContextType {
  lang: Lang;
  setLang: (lang: Lang) => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  t: Translations;
  isRtl: boolean;
}

const SettingsContext = createContext<SettingsContextType>({
  lang: 'en',
  setLang: () => {},
  theme: 'dark',
  setTheme: () => {},
  t: translations.en,
  isRtl: false,
});

export const useSettings = () => useContext(SettingsContext);

interface SettingsProviderProps {
  children: ReactNode;
}

export const SettingsProvider: React.FC<SettingsProviderProps> = ({ children }) => {
  const [lang, setLang] = useState<Lang>(() => {
    return (localStorage.getItem('trinity_lang') as Lang) || 'en';
  });
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem('trinity_theme') as Theme) || 'dark';
  });

  const isRtl = lang === 'he';
  const t = translations[lang];

  useEffect(() => {
    localStorage.setItem('trinity_lang', lang);
    document.documentElement.dir = isRtl ? 'rtl' : 'ltr';
    document.documentElement.lang = lang;
  }, [lang, isRtl]);

  useEffect(() => {
    localStorage.setItem('trinity_theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'light') {
      document.body.classList.add('light-theme');
      document.body.classList.remove('dark-theme');
    } else {
      document.body.classList.add('dark-theme');
      document.body.classList.remove('light-theme');
    }
  }, [theme]);

  return (
    <SettingsContext.Provider value={{ lang, setLang, theme, setTheme, t, isRtl }}>
      {children}
    </SettingsContext.Provider>
  );
};
