"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { AdminNav } from "@/components/admin-nav";
import { useLanguage } from "@/components/language-provider";
import {
  createAdminUser,
  getCurrentUser,
  listAdminKnowledgeBases,
  listAdminUsers,
  resetAdminUserPassword,
  setAdminUserKnowledgeBases,
  setAdminUserStatus,
  type AdminUser,
  type CurrentUser,
  type KnowledgeBase,
} from "@/lib/api";

function UserCard({ user, currentUser, knowledgeBases, onChanged }: { user: AdminUser; currentUser: CurrentUser; knowledgeBases: KnowledgeBase[]; onChanged: () => Promise<void> }) {
  const { t } = useLanguage();
  const [access, setAccess] = useState(user.knowledge_base_ids);
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setNotice("");
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <article className={`admin-user-card${user.active ? "" : " disabled"}`}>
      <header>
        <div>
          <span className="profile-purpose">{t(user.role === "admin" ? "管理员" : "普通用户")}</span>
          <h3>{user.display_name}</h3>
          <p>@{user.username}</p>
        </div>
        <span className={`user-status ${user.active ? "active" : "inactive"}`}>{t(user.active ? "正常" : "已禁用")}</span>
      </header>

      <fieldset className="permission-fieldset" disabled={Boolean(busy) || user.role === "admin"}>
        <legend>{t("知识库权限")}{user.role === "admin" ? t("（管理员默认拥有全部权限）") : ""}</legend>
        {knowledgeBases.map((item) => (
          <label key={item.id}>
            <input
              type="checkbox"
              checked={user.role === "admin" || access.includes(item.id)}
              onChange={(event) => setAccess((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))}
            />
            <span><strong>{item.name}</strong><small>{item.description}</small></span>
          </label>
        ))}
      </fieldset>

      <div className="password-reset-row">
        <label><span>{t("重置密码")}</span><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} placeholder={t("至少 10 个字符")} autoComplete="new-password" /></label>
        <button
          type="button"
          disabled={Boolean(busy) || newPassword.length < 10}
          onClick={() => run("password", async () => {
            await resetAdminUserPassword(user.id, newPassword);
            setNewPassword("");
            setNotice("密码已重置，原有会话已失效");
          })}
        >{t(busy === "password" ? "重置中…" : "重置密码")}</button>
      </div>

      {notice ? <p className="form-notice">{t(notice)}</p> : null}
      {error ? <p className="form-error" role="alert">{t(error)}</p> : null}

      <footer className="profile-actions">
        {user.role !== "admin" ? (
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => run("access", async () => {
              await setAdminUserKnowledgeBases(user.id, access);
              setNotice("知识库权限已更新");
              await onChanged();
            })}
          >{t(busy === "access" ? "保存中…" : "保存知识库权限")}</button>
        ) : null}
        <button
          className={user.active ? "danger" : "primary"}
          type="button"
          disabled={Boolean(busy) || user.id === currentUser.id}
          title={user.id === currentUser.id ? t("不能禁用当前登录账户") : undefined}
          onClick={() => run("status", async () => {
            await setAdminUserStatus(user.id, !user.active);
            await onChanged();
          })}
        >{t(busy === "status" ? "处理中…" : user.active ? "禁用用户" : "启用用户")}</button>
      </footer>
    </article>
  );
}

export default function AdminUsersPage() {
  const { t } = useLanguage();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "user" as "admin" | "user", knowledge_base_ids: [] as string[] });

  const load = useCallback(async () => {
    const [nextUsers, nextKnowledgeBases] = await Promise.all([listAdminUsers(), listAdminKnowledgeBases()]);
    setUsers(nextUsers);
    setKnowledgeBases(nextKnowledgeBases);
  }, []);

  useEffect(() => {
    getCurrentUser().then(async (user) => {
      if (!user) return window.location.replace("/login");
      if (user.role !== "admin") return window.location.replace("/");
      setCurrentUser(user);
      await load();
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, [load]);

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await createAdminUser(form);
      setForm({ username: "", display_name: "", password: "", role: "user", knowledge_base_ids: [] });
      setShowCreate(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    }
  }

  if (!currentUser) return <main id="main-content" tabIndex={-1} className="auth-status"><span className="auth-spinner" /><p>{t(error || "正在验证管理员权限…")}</p></main>;

  return (
    <main id="main-content" tabIndex={-1} className="admin-shell">
      <header className="admin-header">
        <div><p className="section-kicker">{t("账号与授权")}</p><h1>{t("用户与知识库权限")}</h1><p>{t("普通用户只能访问管理员明确授权的知识库。")}</p></div>
        <button className="primary-action" type="button" aria-expanded={showCreate} aria-controls="create-user" onClick={() => setShowCreate((value) => !value)}>{t(showCreate ? "取消新增" : "新增用户")}</button>
      </header>
      <AdminNav />

      {error ? <p className="admin-error" role="alert">{t(error)}</p> : null}

      {showCreate ? (
        <form id="create-user" className="create-profile-form create-user-form" onSubmit={createUser}>
          <div><p className="section-kicker">{t("新增账号")}</p><h2>{t("创建用户")}</h2></div>
          <label><span>{t("用户名")}</span><input required pattern="[A-Za-z0-9._-]{2,80}" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
          <label><span>{t("显示名称")}</span><input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
          <label><span>{t("初始密码")}</span><input required minLength={10} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /></label>
          <label><span>{t("角色")}</span><select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as "admin" | "user" })}><option value="user">{t("普通用户")}</option><option value="admin">{t("管理员")}</option></select></label>
          {form.role === "user" ? (
            <fieldset className="create-access-fieldset">
              <legend>{t("初始知识库权限")}</legend>
              {knowledgeBases.map((item) => <label key={item.id}><input type="checkbox" checked={form.knowledge_base_ids.includes(item.id)} onChange={(event) => setForm({ ...form, knowledge_base_ids: event.target.checked ? [...form.knowledge_base_ids, item.id] : form.knowledge_base_ids.filter((id) => id !== item.id) })} />{item.name}</label>)}
            </fieldset>
          ) : null}
          <button className="primary" type="submit">{t("创建用户")}</button>
        </form>
      ) : null}

      <section className="profile-section">
        <div className="admin-section-heading"><p className="section-kicker">{t("账号目录")}</p><h2>{t("全部用户")} · {String(users.length).padStart(2, "0")}</h2></div>
        <div className="admin-user-list">
          {users.map((user) => <UserCard key={user.id} user={user} currentUser={currentUser} knowledgeBases={knowledgeBases} onChanged={load} />)}
        </div>
      </section>
    </main>
  );
}
