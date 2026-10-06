"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, type CurrentUser } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function AccountPage() {
  const { locale, changeLocale, saving } = useLanguage();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const en = locale === "en";
  useEffect(() => {
    void getCurrentUser().then((current) => {
      if (!current) window.location.replace("/login");
      else setUser(current);
    }).catch(() => setError("无法加载账号 / Could not load account"));
  }, []);
  return <main className="account-page">
    <a href="/">{en ? "← Back to workspace" : "← 返回工作台"}</a>
    <h1>{en ? "User center" : "用户中心"}</h1>
    {error ? <p role="alert">{error}</p> : null}
    {user ? <>
      <dl><dt>{en ? "Display name" : "显示名称"}</dt><dd>{user.display_name}</dd><dt>{en ? "Username" : "用户名"}</dt><dd>{user.username}</dd></dl>
      <h2>{en ? "Display language" : "显示语言"}</h2>
      <p>{en ? "Saved to your account and applied when you sign in on another browser." : "保存到账号，换浏览器登录也会生效。"}</p>
      <label htmlFor="account-language">{en ? "Language" : "语言"}</label>{" "}
      <select id="account-language" value={locale} disabled={saving} onChange={(event) => {
        const next = event.target.value === "en" ? "en" : "zh-CN";
        setSaved(false);
        void changeLocale(next).then(() => setSaved(true)).catch(() => {});
      }}><option value="zh-CN">简体中文</option><option value="en">English</option></select>
      <p role="status">{saving ? (en ? "Saving…" : "保存中…") : saved ? (en ? "Saved" : "已保存") : ""}</p>
    </> : <p>{en ? "Loading…" : "加载中…"}</p>}
  </main>;
}
