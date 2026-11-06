"use client";
import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Modal } from "@/components/modal";
import { AdminPage } from "../../../components/admin-page";
import { useLanguage } from "@/components/language-provider";
import { deleteKnowledgeBase, createKnowledgeBase, getCurrentUser, listAdminKnowledgeBases, type KnowledgeBase } from "@/lib/api";

export default function KnowledgeBasesPage() {
  const { locale } = useLanguage();
  const en = locale === "en";
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({ id: "", name: "", description: "" });
  useEffect(() => {
    let cancelled = false;
    void getCurrentUser().then(async (user) => {
      if (!user) { window.location.replace("/login"); return; }
      if (user.role !== "admin") { window.location.replace("/"); return; }
      const bases = await listAdminKnowledgeBases(true);
      if (!cancelled) { setItems(bases); setReady(true); }
    }).catch(() => { if (!cancelled) setError("load"); });
    return () => { cancelled = true; };
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !form.name.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const item = await createKnowledgeBase({ ...form, name: form.name.trim(), description: form.description.trim() });
      setItems((current) => [...current, item].sort((a, b) => a.name.localeCompare(b.name)));
      setForm({ id: "", name: "", description: "" }); setNotice(item.name);
    } catch (reason) { setError(reason instanceof Error && reason.message.includes("标识已存在") ? "duplicate" : "create"); }
    finally { setBusy(false); }
  }
  return <AdminPage className="kb-page" title={en ? "Knowledge bases" : "知识库管理"} description={en ? "Create a workspace, then upload documents and grant access." : "先创建知识库，再导入资料并分配用户权限。"}>
    {error ? <p className="form-error" role="alert">{error === "duplicate" ? (en ? "Identifier already exists." : "知识库标识已存在，请换一个。") : (en ? "Operation failed. Refresh or check your input and retry." : "操作失败，请刷新或检查填写内容后重试。")}</p> : null}
    {!ready && !error ? <p role="status">{en ? "Loading…" : "加载中…"}</p> : null}
    {ready ? <>
      <details className="model-profile-card kb-create-panel"><summary>{en ? "Create knowledge base" : "新建知识库"}</summary>
        <form className="create-profile-form kb-create-form" onSubmit={submit} aria-busy={busy}>
          <div className="kb-field"><label htmlFor="kb-name">{en ? "Name" : "知识库名称"}</label><input id="kb-name" required maxLength={150} disabled={busy} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div className="kb-field"><label htmlFor="kb-id">{en ? "Identifier" : "知识库标识"}</label><input id="kb-id" required maxLength={128} pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,127}" aria-describedby="kb-id-help" disabled={busy} value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} />
          <p id="kb-id-help">{en ? "Letters, numbers, underscores and hyphens; start with a letter or number. Example: research-2026." : "以字母或数字开头，仅允许字母、数字、下划线和连字符。例如 research-2026。"}</p></div>
          <div className="kb-field kb-wide"><label htmlFor="kb-description">{en ? "Description (optional)" : "描述（可选）"}</label><textarea id="kb-description" maxLength={2000} disabled={busy} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <button className="primary" type="submit" disabled={busy || !form.name.trim() || !form.id}>{busy ? (en ? "Creating…" : "创建中…") : (en ? "Create knowledge base" : "创建知识库")}</button>
        </form>
      </details>
      <p role="status">{notice ? (en ? `Created: ${notice}` : `已创建：${notice}`) : ""}</p>
      <section className="profile-section" aria-labelledby="kb-list-title"><h2 id="kb-list-title">{en ? "Existing knowledge bases" : "已有知识库"}</h2><div className="kb-grid">{items.map((item) => <article className="model-profile-card" key={item.id}><h3>{item.name}</h3><p>{item.id}</p>{!item.active ? <p className="form-error">{en ? "Deletion incomplete · retry deletion to finish cleanup" : "删除尚未完成 · 请重试删除以完成清理"}</p> : null}{item.description ? <p>{item.description}</p> : null}<div className="profile-actions"><button className="danger" type="button" onClick={() => { setDeleting(item); setConfirmation(""); setDeleteError(""); }}>{en ? "Delete" : "删除"}</button></div></article>)}</div></section>
      <p>{en ? "New knowledge bases are visible to administrators only until access is granted." : "新知识库默认仅管理员可见，普通用户需要单独授权。"}</p><nav className="admin-navigation"><Link href="/admin/documents">{en ? "Upload documents" : "导入资料"}</Link><Link href="/admin/users">{en ? "Grant user access" : "分配用户权限"}</Link></nav>
    </> : null}
    {deleting ? <Modal label={en ? "Delete knowledge base" : "删除知识库"} onClose={() => { if (!deleteBusy) setDeleting(null); }} className="kb-delete-modal">
      <h2>{en ? "Delete knowledge base" : "删除知识库"}</h2>
      <p>{en ? "Documents, vectors, uploaded originals and access grants will be removed. Conversation history is retained. This cannot be undone." : "将删除文档、向量、上传原件及权限，保留用户会话历史。此操作无法撤销。"}</p>
      <p>{en ? "Type exactly:" : "请输入以下完整文字："}<strong className="kb-confirm-text">{`我确认删除(${deleting.name})`}</strong></p>
      <label htmlFor="kb-delete-confirm">{en ? "Confirmation" : "确认文字"}</label>
      <input id="kb-delete-confirm" value={confirmation} disabled={deleteBusy} autoComplete="off" onChange={(event) => setConfirmation(event.target.value)} />
      {deleteError ? <p className="form-error" role="alert">{deleteError}</p> : null}
      <div className="profile-actions"><button data-initial-focus type="button" disabled={deleteBusy} onClick={() => setDeleting(null)}>{en ? "Cancel" : "取消"}</button><button className="danger" type="button" disabled={deleteBusy || confirmation !== `我确认删除(${deleting.name})`} onClick={async () => {
        setDeleteBusy(true); setDeleteError("");
        try { await deleteKnowledgeBase(deleting.id, confirmation); setItems((current) => current.filter((item) => item.id !== deleting.id)); setDeleting(null); }
        catch (reason) {
          const message = reason instanceof Error ? reason.message : "";
          const translations: Record<string, string> = {
            "确认文字不匹配": "Confirmation does not match.",
            "仍有导入或索引任务运行，请完成后再删除": "Import or indexing tasks are still running. Wait for them to finish before deleting.",
            "清理未完成，知识库保持停用，可重试删除": "Cleanup is incomplete. The knowledge base remains disabled; retry deletion to finish cleanup.",
          };
          setDeleteError(en ? (translations[message] ?? "Deletion failed. Check running tasks and retry.") : (message || "删除失败，请检查运行任务后重试。"));
          try { setItems(await listAdminKnowledgeBases(true)); }
          catch { /* Keep the deletion error visible if refreshing also fails. */ }
        }
        finally { setDeleteBusy(false); }
      }}>{deleteBusy ? (en ? "Deleting…" : "删除中…") : (en ? "Permanently delete" : "确认删除")}</button></div>
    </Modal> : null}
  </AdminPage>;
}
