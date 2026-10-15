"use client";

import { useId, useState } from "react";
import { useLanguage } from "@/components/language-provider";
import { useConversationHistory } from "@/components/use-conversation-history";
import { logout, type CurrentUser, type KnowledgeBase } from "@/lib/api";

type Props = { user: CurrentUser; knowledgeBases: KnowledgeBase[]; history: ReturnType<typeof useConversationHistory>; busy: boolean; onDelete: () => void; onNavigate: () => void; serviceState: string; statusLabel: string };

export function WorkspaceNavigation({ user, knowledgeBases, history, busy, onDelete, onNavigate, serviceState, statusLabel }: Props) {
  const { t } = useLanguage();
  const knowledgeBaseControlId = useId();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const items = history.active && !history.items.some(item => item.id === history.active?.id) ? [history.active, ...history.items] : history.items;
  const disabled = busy || signingOut;
  return <nav className="workspace-navigation" aria-label={t("工作区导航")}>
    <section className="workspace-nav-context">
      <label htmlFor={knowledgeBaseControlId}>{t("当前资料库")}</label>
      {knowledgeBases.length > 1 ? <select id={knowledgeBaseControlId} value={history.knowledgeBaseId} disabled={disabled} onChange={event => { history.changeKnowledgeBase(event.target.value); onNavigate(); }}>
        {knowledgeBases.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select> : <strong>{knowledgeBases[0].name}</strong>}
      <button className="workspace-nav-new" disabled={disabled} type="button" onClick={() => { history.startNew(); onNavigate(); }}><span aria-hidden="true">＋</span> {t("新建会话")}</button>
    </section>
    {history.enabled ? <section className="workspace-nav-history">
      <div className="workspace-nav-heading"><h2>{t("会话历史")}</h2><button type="button" disabled={disabled || history.listing} onClick={history.refresh}>{t(history.listing ? "加载中…" : "刷新列表")}</button></div>
      {!items.length ? <p>{t(history.listing ? "加载中…" : "暂无会话历史")}</p> : <ul>{items.map(item => <li key={item.id}><button type="button" disabled={disabled} aria-current={history.active?.id === item.id ? "true" : undefined} onClick={async () => { if (await history.open(item.id)) onNavigate(); }}><span className="workspace-nav-title">{item.title}</span><small>{item.turn_count} {t("轮问答")}</small></button></li>)}</ul>}
      {history.hasMore ? <button type="button" disabled={disabled || history.listing} onClick={() => void history.loadMore()}>{t("加载更多")}</button> : null}
      {history.error ? <p role="alert">{t(history.error)}</p> : null}
      <button className="workspace-nav-danger" type="button" disabled={disabled || !history.active} onClick={onDelete}>{t("删除会话")}</button>
      <p className="workspace-nav-hint">{t("会话仅对当前账号可见；历史答案及引用保留生成时的内容，本轮仍独立检索。")}</p>
    </section> : null}
    {user.role === "admin" ? <section className="workspace-nav-admin" aria-label={t("管理导航")}><a href="/admin/documents">{t("文件导入")}</a><a href="/admin/models">{t("管理后台")}</a></section> : null}
    <section className="workspace-nav-account">{user.role === "admin" ? <div className={`service-pill ${serviceState}`}><span aria-hidden="true" />{statusLabel}</div> : null}<strong>{user.display_name}</strong><a href="/account">{t("用户中心")}</a><button type="button" disabled={disabled} onClick={async () => { setSigningOut(true); setLogoutError(false); try { await logout(); window.location.assign("/login"); } catch { setLogoutError(true); setSigningOut(false); } }}>{t(signingOut ? "正在退出…" : "退出")}</button>{logoutError ? <p role="alert">{t("退出失败，请重试")}</p> : null}</section>
  </nav>;
}
