"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminNav } from "@/components/admin-nav";
import { useLanguage } from "@/components/language-provider";
import {
  getCurrentUser,
  listAdminKnowledgeBases,
  listAdminUsers,
  type AdminUser,
  type CurrentUser,
  type KnowledgeBase,
} from "@/lib/api";
import { UserDialogs, type DialogState } from "./user-dialogs";
import { UserList } from "./user-list";
import "./users.css";

type LoadStatus = "loading" | "ready" | "error";

export default function AdminUsersPage() {
  const { t } = useLanguage();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authError, setAuthError] = useState("");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [usersStatus, setUsersStatus] = useState<LoadStatus>("loading");
  const [basesStatus, setBasesStatus] = useState<LoadStatus>("loading");
  const [usersError, setUsersError] = useState("");
  const [basesError, setBasesError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [notice, setNotice] = useState("");

  const loadUsers = useCallback(async () => {
    setUsersStatus("loading");
    setUsersError("");
    try {
      setUsers(await listAdminUsers());
      setUsersStatus("ready");
    } catch (reason) {
      setUsersError(reason instanceof Error ? reason.message : "加载失败");
      setUsersStatus("error");
    }
  }, []);
  const loadBases = useCallback(async () => {
    setBasesStatus("loading");
    setBasesError("");
    try {
      setKnowledgeBases(await listAdminKnowledgeBases());
      setBasesStatus("ready");
    } catch (reason) {
      setBasesError(reason instanceof Error ? reason.message : "加载失败");
      setBasesStatus("error");
    }
  }, []);

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then((user) => {
        if (!active) return;
        if (!user) return window.location.replace("/login");
        if (user.role !== "admin") return window.location.replace("/");
        setCurrentUser(user);
        void loadUsers();
        void loadBases();
      })
      .catch((reason) => {
        if (active)
          setAuthError(reason instanceof Error ? reason.message : "加载失败");
      });
    return () => {
      active = false;
    };
  }, [loadUsers, loadBases]);

  const visibleUsers = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return users.filter(
      (user) =>
        (user.username.toLocaleLowerCase().includes(keyword) ||
          user.display_name.toLocaleLowerCase().includes(keyword)) &&
        (roleFilter === "all" || user.role === roleFilter) &&
        (statusFilter === "all" || user.active === (statusFilter === "active")),
    );
  }, [users, query, roleFilter, statusFilter]);

  if (!currentUser)
    return (
      <main id="main-content" tabIndex={-1} className="auth-status">
        <span className="auth-spinner" />
        <p role={authError ? "alert" : undefined}>
          {t(authError || "正在验证管理员权限…")}
        </p>
      </main>
    );

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="admin-shell user-management"
    >
      <header className="admin-header">
        <div>
          <p className="section-kicker">{t("账号与授权")}</p>
          <h1>{t("用户管理")}</h1>
          <p>{t("普通用户只能访问管理员明确授权的知识库。")}</p>
        </div>
        <button
          className="primary-action"
          type="button"
          disabled={basesStatus !== "ready"}
          onClick={() => setDialog({ kind: "create" })}
        >
          {t("新增用户")}
        </button>
      </header>
      <AdminNav />
      {basesStatus === "error" ? (
        <p className="admin-error" role="alert">
          {t("知识库加载失败，授权操作暂不可用")}：{t(basesError)}{" "}
          <button type="button" onClick={() => void loadBases()}>
            {t("重试")}
          </button>
        </p>
      ) : null}
      {notice ? (
        <p className="form-notice" role="status">
          {t(notice)}
        </p>
      ) : null}
      <section className="profile-section">
        <div className="admin-section-heading">
          <p className="section-kicker">{t("账号目录")}</p>
          <h2>
            {t("全部用户")} ·{" "}
            {usersStatus === "ready"
              ? String(users.length).padStart(2, "0")
              : "—"}
          </h2>
        </div>
        <div className="user-directory-filters">
          <label>
            <span>{t("搜索用户")}</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("用户名或显示名称")}
            />
          </label>
          <label>
            <span>{t("角色")}</span>
            <select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
            >
              <option value="all">{t("全部角色")}</option>
              <option value="admin">{t("管理员")}</option>
              <option value="user">{t("普通用户")}</option>
            </select>
          </label>
          <label>
            <span>{t("状态")}</span>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="all">{t("全部状态")}</option>
              <option value="active">{t("正常")}</option>
              <option value="inactive">{t("已禁用")}</option>
            </select>
          </label>
        </div>
        {usersStatus === "loading" ? (
          <p role="status">{t("正在加载用户…")}</p>
        ) : null}
        {usersStatus === "error" ? (
          <p className="admin-error" role="alert">
            {t(usersError)}{" "}
            <button type="button" onClick={() => void loadUsers()}>
              {t("重试")}
            </button>
          </p>
        ) : null}
        {usersStatus === "ready" && users.length === 0 ? (
          <p>{t("暂无用户")}</p>
        ) : null}
        {usersStatus === "ready" &&
        users.length > 0 &&
        visibleUsers.length === 0 ? (
          <p>{t("没有符合条件的用户，请调整筛选。")}</p>
        ) : null}
        {usersStatus === "ready" && visibleUsers.length > 0 ? (
          <UserList
            users={visibleUsers}
            currentUserId={currentUser.id}
            t={t}
            onAction={(kind, userId) => {
              setNotice("");
              setDialog({ kind, userId });
            }}
          />
        ) : null}
      </section>
      {dialog ? (
        <UserDialogs
          key={`${dialog.kind}-${"userId" in dialog ? dialog.userId : "new"}`}
          dialog={dialog}
          user={
            "userId" in dialog
              ? users.find((item) => item.id === dialog.userId)
              : undefined
          }
          currentUserId={currentUser.id}
          knowledgeBases={knowledgeBases}
          basesReady={basesStatus === "ready"}
          onClose={() => setDialog(null)}
          onNotice={setNotice}
          onCreated={(user: AdminUser) => setUsers((current) => [...current, user])}
          onUpdated={(user: AdminUser) =>
            setUsers((current) =>
              current.map((item) => (item.id === user.id ? user : item)),
            )
          }
        />
      ) : null}
    </main>
  );
}
