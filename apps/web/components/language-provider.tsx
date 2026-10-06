"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { translate, type Locale } from "@/lib/i18n";
import { getCurrentUser, updateLocalePreference } from "@/lib/api";

type LanguageContextValue = { locale: Locale; t: (text: string) => string; changeLocale: (next: Locale) => Promise<void>; saving: boolean; error: string };
const LanguageContext = createContext<LanguageContextValue | null>(null);
const STORAGE_KEY = "knowledge-rag-language";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>("zh-CN");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "en" || saved === "zh-CN") setLocale(saved);
    } catch { /* Storage may be disabled; Chinese remains the default. */ }
    void getCurrentUser().then((user) => { if (user) setLocale(user.preferred_locale); }).catch(() => {});
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  async function changeLocale(next: Locale) {
    setSaving(true);
    setError("");
    try {
      const user = await getCurrentUser();
      if (user) await updateLocalePreference(next);
      setLocale(next);
      try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* Keep in memory. */ }
    } catch (reason) {
      setError(locale === "en" ? "Language could not be saved. Please retry." : "语言保存失败，请重试。");
      throw reason;
    } finally { setSaving(false); }
  }

  return (
    <LanguageContext.Provider value={{ locale, changeLocale, saving, error, t: (text) => translate(locale, text) }}>
      {children}
      {error ? <p role="alert">{error}</p> : null}
      <button className="language-switch" type="button" disabled={saving} onClick={() => { void changeLocale(locale === "zh-CN" ? "en" : "zh-CN").catch(() => {}); }} aria-label={locale === "zh-CN" ? "Switch to English" : "切换为中文"} title={locale === "zh-CN" ? "Switch to English" : "切换为中文"}>
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
