"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { translate, type Locale } from "@/lib/i18n";
import { getCurrentUser, updateLocalePreference } from "@/lib/api";

type LanguageContextValue = { locale: Locale; t: (text: string) => string; changeLocale: (next: Locale) => Promise<void>; saving: boolean; error: string };
const LanguageContext = createContext<LanguageContextValue | null>(null);
const STORAGE_KEY = "knowledge-rag-language";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
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
      setError("save");
      throw reason;
    } finally { setSaving(false); }
  }

  return (
    <LanguageContext.Provider value={{ locale, changeLocale, saving, error, t: (text) => translate(locale, text) }}>
      <a className="skip-link" href="#main-content">{locale === "en" ? "Skip to content" : "跳转到主要内容"}</a>
      <header className="site-masthead">
        <Link className="site-identity" href="/">
          <span className="site-monogram" aria-hidden="true">G</span>
          <span><strong>{locale === "en" ? "Knowledge Base" : "知识库"}</strong><small>{locale === "en" ? "Research · Documents · Evidence" : "研究 · 资料 · 证据"}</small></span>
        </Link>
        <nav className="site-navigation" aria-label={locale === "en" ? "Site navigation" : "网站导航"}>
          <Link href="/" aria-current={pathname === "/" ? "page" : undefined}>{locale === "en" ? "Workspace" : "工作台"}</Link>
          <Link href="/account" aria-current={pathname === "/account" ? "page" : undefined}>{locale === "en" ? "Account" : "用户中心"}</Link>
          <button className="language-switch" type="button" disabled={saving} onClick={() => { void changeLocale(locale === "zh-CN" ? "en" : "zh-CN").catch(() => {}); }} aria-label={locale === "en" ? "Change language" : "切换语言"}>
            {saving ? (locale === "en" ? "Saving…" : "保存中…") : (locale === "en" ? "English" : "中文")}<span aria-hidden="true"> ⇄</span>
          </button>
        </nav>
      </header>
      {error ? <p className="site-language-error" role="alert">{locale === "en" ? "Language could not be saved. Please retry." : "语言保存失败，请重试。"}</p> : null}
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("LanguageProvider is missing");
  return context;
}
