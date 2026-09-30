"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { translate, type Locale } from "@/lib/i18n";

type LanguageContextValue = { locale: Locale; t: (text: string) => string };
const LanguageContext = createContext<LanguageContextValue | null>(null);
const STORAGE_KEY = "knowledge-rag-language";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>("zh-CN");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "en" || saved === "zh-CN") setLocale(saved);
    } catch { /* Storage may be disabled; Chinese remains the default. */ }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  function toggle() {
    const next = locale === "zh-CN" ? "en" : "zh-CN";
    setLocale(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* Keep the in-memory choice. */ }
  }

  return (
    <LanguageContext.Provider value={{ locale, t: (text) => translate(locale, text) }}>
      {children}
      <button className="language-switch" type="button" onClick={toggle} aria-label={locale === "zh-CN" ? "Switch to English" : "切换为中文"} title={locale === "zh-CN" ? "Switch to English" : "切换为中文"}>
        {locale === "zh-CN" ? "EN" : "中文"}
      </button>
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("LanguageProvider is missing");
  return context;
}
