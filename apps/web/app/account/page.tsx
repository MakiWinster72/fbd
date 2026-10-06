"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, updateAccountProfile, changeAccountPassword, type CurrentUser } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function AccountPage() {
  const { locale, changeLocale, saving } = useLanguage();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const en = locale === "en";
  useEffect(() => {
    void getCurrentUser().then((current) => {
      if (!current) window.location.replace("/login");
      else { setUser(current); setName(current.display_name); }
    }).catch(() => setError("load"));
  }, []);
  return <main className="account-page">
    <a href="/">{en ? "← Back to workspace" : "← 返回工作台"}</a>
    <h1>{en ? "User center" : "用户中心"}</h1>
    {error ? <p role="alert">{({ load: en ? "Could not load account" : "无法加载账号", name: en ? "Could not save display name" : "显示名称保存失败", mismatch: en ? "Passwords do not match" : "两次新密码不一致", password: en ? "Password change failed. Check your current password and use a different new password." : "修改失败，请检查当前密码，并使用不同的新密码。" } as Record<string, string>)[error]}</p> : null}
    {user ? <>
      <dl><dt>{en ? "Display name" : "显示名称"}</dt><dd>{user.display_name}</dd><dt>{en ? "Username" : "用户名"}</dt><dd>{user.username}</dd></dl>
      <form onSubmit={async (event) => {
        event.preventDefault(); setBusy(true); setError(""); setNotice("");
        try { setUser(await updateAccountProfile(name)); setNotice("name-saved"); }
        catch { setError("name"); }
        finally { setBusy(false); }
      }}>
        <label htmlFor="display-name">{en ? "Display name" : "显示名称"}</label>
        <input id="display-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} />
        <button disabled={busy} type="submit">{en ? "Save name" : "保存名称"}</button>
      </form>
      <p role="status">{notice ? (en ? "Display name saved" : "显示名称已保存") : ""}</p>
      <h2>{en ? "Change password" : "修改密码"}</h2>
      <p>{en ? "All sessions will be signed out after the password is changed." : "修改成功后将注销所有登录会话，请重新登录。"}</p>
      <form onSubmit={async (event) => {
        event.preventDefault(); setError("");
        if (newPassword !== confirmation) { setError("mismatch"); return; }
        setBusy(true);
        try { await changeAccountPassword(currentPassword, newPassword); window.location.assign("/login"); }
        catch { setError("password"); }
        finally { setBusy(false); }
      }}>
        <label htmlFor="current-password">{en ? "Current password" : "当前密码"}</label>
        <input id="current-password" type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        <label htmlFor="new-password">{en ? "New password (8–128 characters)" : "新密码（8–128 个字符）"}</label>
        <input id="new-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
        <label htmlFor="confirm-password">{en ? "Confirm new password" : "确认新密码"}</label>
        <input id="confirm-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <button disabled={busy} type="submit">{en ? "Change password and sign out" : "修改密码并退出"}</button>
      </form>
      <h2>{en ? "Display language" : "显示语言"}</h2>
      <p>{en ? "Saved to your account and applied when you sign in on another browser." : "保存到账号，换浏览器登录也会生效。"}</p>
      <label htmlFor="account-language">{en ? "Language" : "语言"}</label>{" "}
      <select id="account-language" value={locale} disabled={saving} onChange={(event) => {
        const next = event.target.value === "en" ? "en" : "zh-CN";
        setSaved(false);
        void changeLocale(next).then(() => setSaved(true)).catch(() => {});
      }}><option value="zh-CN">{en ? "Simplified Chinese" : "简体中文"}</option><option value="en">{en ? "English" : "英语"}</option></select>
      <p role="status">{saving ? (en ? "Saving…" : "保存中…") : saved ? (en ? "Saved" : "已保存") : ""}</p>
    </> : <p>{en ? "Loading…" : "加载中…"}</p>}
  </main>;
}
