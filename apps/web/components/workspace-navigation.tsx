"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useLanguage } from "@/components/language-provider";
import { useConversationHistory } from "@/components/use-conversation-history";
import { logout, type CurrentUser, type KnowledgeBase } from "@/lib/api";

type Props = {
  user: CurrentUser;
  knowledgeBases: KnowledgeBase[];
  history: ReturnType<typeof useConversationHistory>;
  busy: boolean;
  onDelete: () => void;
  onNavigate: () => void;
  serviceState: string;
  statusLabel: string;
};

function NavIcon({
  kind,
}: {
  kind: "plus" | "refresh" | "chat" | "trash" | "chevron";
}) {
  const icons = {
    plus: "plus-lg",
    refresh: "arrow-clockwise",
    chat: "chat-left-text",
    trash: "trash3",
    chevron: "chevron-down",
  };
  return (
    <i
      aria-hidden="true"
      className={`workspace-nav-icon bi bi-${icons[kind]}`}
    />
  );
}

export function WorkspaceNavigation({
  user,
  knowledgeBases,
  history,
  busy,
  onDelete,
  onNavigate,
  serviceState,
  statusLabel,
}: Props) {
  const { t } = useLanguage();
  const knowledgeBaseControlId = useId();
  const accountMenuRef = useRef<HTMLDetailsElement>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const items =
    history.active &&
    !history.items.some((item) => item.id === history.active?.id)
      ? [history.active, ...history.items]
      : history.items;
  const disabled = busy || signingOut;

  useEffect(() => {
    const dismissOutside = (event: PointerEvent) => {
      const menu = accountMenuRef.current;
      if (
        menu?.open &&
        event.target instanceof Node &&
        !menu.contains(event.target)
      ) {
        menu.open = false;
      }
    };
    document.addEventListener("pointerdown", dismissOutside, true);
    return () =>
      document.removeEventListener("pointerdown", dismissOutside, true);
  }, []);

  return (
    <nav className="workspace-navigation" aria-label={t("工作区导航")}>
      <section className="workspace-nav-context">
        <label htmlFor={knowledgeBaseControlId}>{t("当前资料库")}</label>
        {knowledgeBases.length > 1 ? (
          <select
            id={knowledgeBaseControlId}
            value={history.knowledgeBaseId}
            disabled={disabled}
            onChange={(event) => {
              history.changeKnowledgeBase(event.target.value);
              onNavigate();
            }}
          >
            {knowledgeBases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        ) : (
          <strong className="workspace-nav-library">
            {knowledgeBases[0].name}
          </strong>
        )}
        <button
          className="workspace-nav-new"
          disabled={disabled}
          type="button"
          onClick={() => {
            history.startNew();
            onNavigate();
          }}
        >
          <NavIcon kind="plus" />
          {t("新建会话")}
        </button>
      </section>

      {history.enabled ? (
        <section className="workspace-nav-history">
          <div className="workspace-nav-heading">
            <h2
              title={t(
                "会话仅对当前账号可见；历史答案及引用保留生成时的内容，本轮仍独立检索。",
              )}
            >
              {t("会话历史")}
            </h2>
            <div className="workspace-nav-tools">
              {history.active ? (
                <button
                  className="workspace-nav-danger"
                  type="button"
                  title={t("删除会话")}
                  aria-label={t("删除会话")}
                  disabled={disabled}
                  onClick={onDelete}
                >
                  <NavIcon kind="trash" />
                </button>
              ) : null}
              <button
                type="button"
                title={t(history.listing ? "加载中…" : "刷新列表")}
                aria-label={t(history.listing ? "加载中…" : "刷新列表")}
                disabled={disabled || history.listing}
                onClick={history.refresh}
              >
                <NavIcon kind="refresh" />
              </button>
            </div>
          </div>
          {!items.length ? (
            <div className="workspace-nav-empty">
              <NavIcon kind="chat" />
              <p>{t(history.listing ? "加载中…" : "暂无会话历史")}</p>
            </div>
          ) : (
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-current={
                      history.active?.id === item.id ? "true" : undefined
                    }
                    onClick={async () => {
                      if (await history.open(item.id)) onNavigate();
                    }}
                  >
                    <NavIcon kind="chat" />
                    <span className="workspace-nav-title">{item.title}</span>
                    <small>{item.turn_count}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {history.hasMore ? (
            <button
              className="workspace-nav-more"
              type="button"
              disabled={disabled || history.listing}
              onClick={() => void history.loadMore()}
            >
              {t("加载更多")}
            </button>
          ) : null}
          {history.error ? <p role="alert">{t(history.error)}</p> : null}
        </section>
      ) : null}

      <section className="workspace-nav-account">
        <details ref={accountMenuRef} className="workspace-account-menu">
          <summary>
            <span className="workspace-account-avatar" aria-hidden="true">
              {user.display_name.slice(0, 1).toUpperCase()}
            </span>
            <span className="workspace-account-name">
              <strong>{user.display_name}</strong>
              <span>{t("用户中心")}</span>
            </span>
            <NavIcon kind="chevron" />
          </summary>
          <div className="workspace-account-options">
            <a href="/account">{t("用户中心")}</a>
            {user.role === "admin" ? (
              <div className="workspace-nav-admin" aria-label={t("管理导航")}>
                <a href="/admin/users">{t("用户管理")}</a>
                <a href="/admin/documents">{t("文件导入")}</a>
                <a href="/admin/models">{t("管理后台")}</a>
              </div>
            ) : null}
            <button
              type="button"
              disabled={disabled}
              onClick={async () => {
                setSigningOut(true);
                setLogoutError(false);
                try {
                  await logout();
                  window.location.assign("/login");
                } catch {
                  setLogoutError(true);
                  setSigningOut(false);
                }
              }}
            >
              {t(signingOut ? "正在退出…" : "退出")}
            </button>
          </div>
        </details>
        {user.role === "admin" ? (
          <div className={`service-pill ${serviceState}`}>
            <span aria-hidden="true" />
            {statusLabel}
          </div>
        ) : null}
        {logoutError ? <p role="alert">{t("退出失败，请重试")}</p> : null}
      </section>
    </nav>
  );
}
