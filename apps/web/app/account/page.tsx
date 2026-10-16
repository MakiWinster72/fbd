"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, updateAccountProfile, changeAccountPassword, type CurrentUser } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function AccountPage() {
  const { locale, changeLocale, saving, error: languageError } = useLanguage();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState<"name" | "password" | null>(null);
  const [notice, setNotice] = useState("");
  const en = locale === "en";
  useEffect(() => {
    void getCurrentUser().then((current) => {
      if (!current) window.location.replace("/login");
      else { setUser(current); setName(current.display_name); }
    }).catch(() => setError("load"));
  }, []);
  const errors: Record<string, string> = {
    load: en ? "Could not load account. Please reload and try again." : "无法加载账号，请刷新后重试。",
    name: en ? "Could not save display name" : "显示名称保存失败",
    mismatch: en ? "Passwords do not match" : "两次新密码不一致",
    password: en ? "Password change failed. Check your current password and use a different new password." : "修改失败，请检查当前密码，并使用不同的新密码。",
  };
  return <main id="main-content" tabIndex={-1} className="account-page">
    <header className="account-header">
      <div><p className="section-kicker">{en ? "ACCOUNT SETTINGS" : "账号设置"}</p><h1>{en ? "User center" : "用户中心"}</h1></div>
      {user ? <span className="account-role">{user.role === "admin" ? (en ? "Administrator" : "管理员") : (en ? "Member" : "普通用户")}</span> : null}
    </header>
    {error === "load" ? <p className="form-error" role="alert">{errors.load}</p> : null}
    {user ? <div className="account-sections">
      <section className="account-section" aria-labelledby="profile-title">
        <div className="account-section-heading"><span className="section-number" aria-hidden="true">01</span><h2 id="profile-title">{en ? "Personal profile" : "个人资料"}</h2></div>
        <div className="account-section-content">
          <dl><dt>{en ? "Username" : "用户名"}</dt><dd>{user.username}</dd></dl>
          <form aria-busy={busy === "name"} onSubmit={async (event) => {
            event.preventDefault(); if (busy) return; setBusy("name"); setError(""); setNotice("");
            try { const updated = await updateAccountProfile(name.trim()); setUser(updated); setName(updated.display_name); setNotice("name-saved"); }
            catch { setError("name"); }
            finally { setBusy(null); }
          }}>
            <label htmlFor="display-name">{en ? "Display name" : "显示名称"}</label>
            <input id="display-name" autoComplete="nickname" value={name} onChange={(event) => { setName(event.target.value); setNotice(""); }} required maxLength={100} disabled={Boolean(busy)} />
            {error === "name" ? <p className="form-error" role="alert">{errors.name}</p> : null}
            <div className="account-form-footer"><button disabled={Boolean(busy) || !name.trim() || name.trim() === user.display_name} type="submit">{busy === "name" ? (en ? "Saving…" : "保存中…") : (en ? "Save name" : "保存名称")}</button><p role="status">{notice ? (en ? "Display name saved" : "显示名称已保存") : ""}</p></div>
          </form>
        </div>
      </section>
      <section className="account-section" aria-labelledby="language-title">
        <div className="account-section-heading"><span className="section-number" aria-hidden="true">02</span><h2 id="language-title">{en ? "Display language" : "显示语言"}</h2></div>
        <div className="account-section-content language-preference">
          <label htmlFor="account-language">{en ? "Language" : "语言"}</label>
          <select id="account-language" value={locale} disabled={saving} onChange={(event) => {
            const next = event.target.value === "en" ? "en" : "zh-CN";
            setSaved(false);
            void changeLocale(next).then(() => setSaved(true)).catch(() => {});
          }}><option value="zh-CN">{en ? "Simplified Chinese" : "简体中文"}</option><option value="en">{en ? "English" : "英语"}</option></select>
          <p role="status">{saving ? (en ? "Saving…" : "保存中…") : saved ? (en ? "Saved" : "已保存") : ""}</p>
          {languageError ? <p className="form-error" role="alert">{en ? "Language could not be saved. Please retry." : "语言保存失败，请重试。"}</p> : null}
        </div>
      </section>
      <section className="account-section" aria-labelledby="security-title">
        <div className="account-section-heading"><span className="section-number" aria-hidden="true">03</span><h2 id="security-title">{en ? "Sign-in security" : "登录安全"}</h2></div>
        <div className="account-section-content">
          <form aria-busy={busy === "password"} onSubmit={async (event) => {
            event.preventDefault(); if (busy) return; setError("");
            if (newPassword !== confirmation) { setError("mismatch"); return; }
            setBusy("password");
            try { await changeAccountPassword(currentPassword, newPassword); window.location.assign("/login"); }
            catch { setError("password"); }
            finally { setBusy(null); }
          }}>
            <label htmlFor="current-password">{en ? "Current password" : "当前密码"}</label>
            <input id="current-password" type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} disabled={Boolean(busy)} />
            <label htmlFor="new-password">{en ? "New password (8–128 characters)" : "新密码（8–128 个字符）"}</label>
            <input id="new-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} disabled={Boolean(busy)} />
            <label htmlFor="confirm-password">{en ? "Confirm new password" : "确认新密码"}</label>
            <input id="confirm-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={Boolean(busy)} aria-invalid={error === "mismatch"} aria-describedby={error === "mismatch" ? "password-error" : undefined} />
            {error === "password" || error === "mismatch" ? <p id="password-error" className="form-error" role="alert">{errors[error]}</p> : null}
            <button disabled={Boolean(busy)} type="submit">{busy === "password" ? (en ? "Updating…" : "正在修改…") : (en ? "Change password and sign out" : "修改密码并退出")}</button>
          </form>
        </div>
      </section>
    </div> : !error ? <p role="status">{en ? "Loading…" : "加载中…"}</p> : null}
  </main>;
}
