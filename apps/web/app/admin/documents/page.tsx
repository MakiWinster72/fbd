"use client";

import { DragEvent, useEffect, useRef, useState } from "react";
import { deleteIndexedDocument, getCurrentUser, getDocumentImport, getIndexedDocument, getMineruStatus, listAdminKnowledgeBases, listDocumentImports, listIndexedDocuments, listModelProfiles, reindexIndexedDocument, retryDocumentImport, uploadDocument, type DocumentImportJob, type IndexedDocument, type IndexedDocumentDetail, type KnowledgeBase, type ModelProfile } from "@/lib/api";

type IntakeStatus = "ready" | "blocked";

type IntakeFile = {
  id: string;
  file: File;
  status: IntakeStatus;
  reason?: string;
};

const MAX_FILE_BYTES = 200 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = ["pdf", "docx"];

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function extensionOf(name: string) {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function fileId(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function classify(file: File): IntakeFile {
  const extension = extensionOf(file.name);
  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    return { id: fileId(file), file, status: "blocked", reason: extension === "doc" ? "旧版 DOC 请先转换为 DOCX 或 PDF" : "当前入库流程仅支持 PDF 和 DOCX，请先转换格式" };
  }
  if (file.size === 0) {
    return { id: fileId(file), file, status: "blocked", reason: "空文件无法导入" };
  }
  if (file.size > MAX_FILE_BYTES) {
    return { id: fileId(file), file, status: "blocked", reason: "文件超过 200 MB 上限" };
  }
  return { id: fileId(file), file, status: "ready" };
}

export default function AdminDocumentsPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [files, setFiles] = useState<IntakeFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [ocrMode, setOcrMode] = useState("auto");
  const [duplicateMode, setDuplicateMode] = useState("skip");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [selectionNotice, setSelectionNotice] = useState("");
  const [mineruStatus, setMineruStatus] = useState<"checking" | "reachable" | "offline">("checking");
  const [embeddingProfile, setEmbeddingProfile] = useState<ModelProfile | null>(null);
  const [embeddingError, setEmbeddingError] = useState("");
  const [jobs, setJobs] = useState<DocumentImportJob[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [documentDetail, setDocumentDetail] = useState<IndexedDocumentDetail | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [documentBusy, setDocumentBusy] = useState("");
  const [documentRevision, setDocumentRevision] = useState(0);

  useEffect(() => {
    getCurrentUser().then(async (user) => {
      if (!user) return window.location.replace("/login");
      if (user.role !== "admin") return window.location.replace("/");
      const bases = await listAdminKnowledgeBases();
      setKnowledgeBases(bases);
      setKnowledgeBaseId(bases[0]?.id ?? "");
      setReady(true);
      getMineruStatus().then((status) => setMineruStatus(status.reachable ? "reachable" : "offline")).catch(() => setMineruStatus("offline"));
      listModelProfiles().then((profiles) => setEmbeddingProfile(profiles.find((profile) => profile.purpose === "embedding" && profile.is_active) ?? null)).catch(() => setEmbeddingError("无法读取 Embedding 配置，请检查后台接口。"));
      listDocumentImports().then(setJobs).catch(() => setUploadError("无法加载历史导入任务"));
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, []);

  useEffect(() => {
    if (!knowledgeBaseId) return;
    let cancelled = false;
    setDocumentDetail(null);
    listIndexedDocuments(knowledgeBaseId).then((items) => {
      if (!cancelled) { setDocuments(items); setDocumentError(""); }
    }).catch((reason) => { if (!cancelled) setDocumentError(reason instanceof Error ? reason.message : "无法加载文档清单"); });
    return () => { cancelled = true; };
  }, [knowledgeBaseId, documentRevision, jobs.filter((job) => job.status === "completed" || job.status === "deleted").map((job) => `${job.id}:${job.status}`).join(",")]);

  useEffect(() => {
    const active = jobs.filter((job) => !["completed", "failed", "deleted"].includes(job.status));
    if (!active.length) return;
    const timer = window.setTimeout(async () => {
      const updates = await Promise.allSettled(active.map((job) => getDocumentImport(job.id)));
      setJobs((current) => current.map((job) => {
        const index = active.findIndex((item) => item.id === job.id);
        const update = updates[index];
        return index >= 0 && update.status === "fulfilled" ? update.value : job;
      }));
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [jobs]);

  async function showDocument(documentId: string) {
    if (documentDetail?.document_id === documentId) { setDocumentDetail(null); return; }
    setDocumentError("");
    try { setDocumentDetail(await getIndexedDocument(knowledgeBaseId, documentId)); }
    catch (reason) { setDocumentError(reason instanceof Error ? reason.message : "无法读取文档详情"); }
  }

  async function removeDocument(item: IndexedDocument) {
    if (!window.confirm(`确认从当前知识库删除《${item.document_name}》？此操作会清除索引和可重建的切块，无法撤销。`)) return;
    setDocumentBusy(item.document_id);
    setDocumentError("");
    try {
      await deleteIndexedDocument(knowledgeBaseId, item.document_id);
      setDocumentDetail(null);
      setDocumentRevision((value) => value + 1);
      listDocumentImports().then(setJobs).catch(() => {});
    } catch (reason) { setDocumentError(reason instanceof Error ? reason.message : "删除失败"); }
    finally { setDocumentBusy(""); }
  }

  async function reindexDocument(item: IndexedDocument) {
    setDocumentBusy(item.document_id);
    setDocumentError("");
    try {
      const job = await reindexIndexedDocument(knowledgeBaseId, item.document_id);
      setJobs((current) => [job, ...current]);
      setSelectionNotice(`《${item.document_name}》已提交重新向量化任务，请查看下方进度。`);
    } catch (reason) { setDocumentError(reason instanceof Error ? reason.message : "重新向量化失败"); }
    finally { setDocumentBusy(""); }
  }

  async function retryImport(job: DocumentImportJob) {
    setDocumentBusy(job.id);
    setUploadError("");
    try {
      const updated = await retryDocumentImport(job.id);
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setUploadError(`${job.file_name}：${reason instanceof Error ? reason.message : "重试失败"}`);
    } finally { setDocumentBusy(""); }
  }

  async function startImport() {
    if (!readyFiles.length || !knowledgeBaseId || uploading) return;
    setUploading(true);
    setUploadError("");
    const succeeded = new Set<string>();
    try {
      for (const item of readyFiles) {
        try {
          const job = await uploadDocument(item.file, knowledgeBaseId, ocrMode, duplicateMode);
          setJobs((current) => [job, ...current.filter((other) => other.id !== job.id)]);
          succeeded.add(item.id);
        } catch (reason) {
          setUploadError(`${item.file.name}：${reason instanceof Error ? reason.message : "上传失败"}。其余文件将继续上传。`);
        }
      }
      setFiles((current) => current.filter((item) => !succeeded.has(item.id)));
      setReviewed(false);
      setSelectionNotice(succeeded.size ? `已提交 ${succeeded.size} 个文件，后台任务进度见下方。` : "上传失败，请检查提示后重试。");
    } finally {
      setUploading(false);
    }
  }

  function addFiles(selected: FileList | File[]) {
    const chosen = Array.from(selected);
    if (!chosen.length) return;
    setReviewed(false);
    const known = new Set(files.map((item) => item.id));
    const additions = chosen.map(classify).filter((item) => {
      if (known.has(item.id)) return false;
      known.add(item.id);
      return true;
    });
    setSelectionNotice(`已选择 ${additions.length} 个文件${chosen.length > additions.length ? `，跳过 ${chosen.length - additions.length} 个重复项` : ""}。文件仅在本地清单中，尚未上传。`);
    setFiles((current) => [...current, ...additions]);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  const readyFiles = files.filter((item) => item.status === "ready");
  const blockedFiles = files.filter((item) => item.status === "blocked");
  const totalBytes = readyFiles.reduce((total, item) => total + item.file.size, 0);

  if (!ready) return <main className="auth-status"><span className="auth-spinner" /><p>{error || "正在验证管理员权限…"}</p></main>;

  return (
    <main className="admin-shell document-intake-shell">
      <header className="admin-header">
        <div>
          <p className="section-kicker">DOCUMENT INTAKE</p>
          <h1>文件导入</h1>
          <p>先核对文件、解析方式和目标知识库，再交给后台完成解析与向量化。</p>
        </div>
        <nav><a href="/">返回知识库</a><a href="/admin/users">用户管理</a><a href="/admin/models">模型配置</a></nav>
      </header>

      {error ? <p className="admin-error" role="alert">{error}</p> : null}
      <p className={`intake-service-status ${mineruStatus}`} role="status">MinerU 服务：{mineruStatus === "checking" ? "正在检查…" : mineruStatus === "reachable" ? "端口可连接（不代表解析任务已成功）" : "无法连接；PDF 需启动服务或选择纯文本层模式。"}</p>

      <ol className="intake-stage-rail" aria-label="文件导入流程">
        {["选择文件", "解析内容", "生成切块", "写入向量", "完成核验"].map((label, index) => (
          <li className={index === 0 ? "current" : "locked"} key={label}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{label}</strong>
          </li>
        ))}
      </ol>

      <section className="intake-layout">
        <div className="intake-main-column">
          <section className="intake-panel">
            <div className="intake-panel-heading">
              <div><p className="section-kicker">01 / SOURCE FILES</p><h2>选择待导入文件</h2></div>
              <p>支持多选；浏览器只读取文件名称和大小，尚未上传。</p>
            </div>

            {selectionNotice ? <p className="intake-selection-notice" role="status">{selectionNotice}</p> : null}
            <div
              className={`intake-dropzone${dragging ? " dragging" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
              onDrop={onDrop}
            >
              <svg viewBox="0 0 48 48" aria-hidden="true"><path d="M14 5h14l8 8v30H14z" /><path d="M28 5v9h8M24 34V20m-6 6 6-6 6 6" /></svg>
              <h3>将 PDF 或 DOCX 放到这里</h3>
              <p>单个文件不超过 200 MB；其他格式请先转换为 PDF 或 DOCX。</p>
              <p>MinerU 可解析的格式不等于本站已支持的入库格式；后端规范化流程目前仅支持 PDF/DOCX。</p>
              <button type="button" onClick={() => inputRef.current?.click()}>从电脑选择文件</button>
              <input
                className="visually-hidden"
                ref={inputRef}
                type="file"
                accept=".pdf,.docx,.doc"
                multiple
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>
          </section>

          <section className="intake-panel intake-queue-panel">
            <div className="intake-panel-heading">
              <div><p className="section-kicker">02 / INTAKE MANIFEST</p><h2>导入清单 · {String(files.length).padStart(2, "0")}</h2></div>
              {files.length ? <button className="intake-text-button" type="button" onClick={() => { setFiles([]); setReviewed(false); setSelectionNotice(""); }}>清空清单</button> : null}
            </div>

            {!files.length ? (
              <div className="intake-empty-state">
                <span>NO FILES</span>
                <p>选择文件后，这里会逐项显示格式检查、大小和导入状态。</p>
              </div>
            ) : (
              <div className="intake-file-list">
                {files.map((item, index) => (
                  <article className={`intake-file-row ${item.status}`} key={item.id}>
                    <span className="intake-file-index">{String(index + 1).padStart(2, "0")}</span>
                    <div className="intake-file-type">{extensionOf(item.file.name).toUpperCase() || "FILE"}</div>
                    <div className="intake-file-copy">
                      <strong>{item.file.name}</strong>
                      <span>{formatBytes(item.file.size)} · {item.status === "ready" ? "格式检查通过" : item.reason}</span>
                    </div>
                    <span className={`intake-status-mark ${item.status}`}>{item.status === "ready" ? "可导入" : "需处理"}</span>
                    <button className="intake-remove" type="button" aria-label={`移除 ${item.file.name}`} onClick={() => { setFiles((current) => current.filter((file) => file.id !== item.id)); setReviewed(false); }}>
                      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
                    </button>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>

        <aside className="intake-sidebar">
          <section className="intake-config-card">
            <p className="section-kicker">IMPORT SETTINGS</p>
            <h2>处理设置</h2>
            <div className="intake-embedding-note"><strong>当前 Embedding</strong><span>{embeddingError || (embeddingProfile ? `${embeddingProfile.name} · ${embeddingProfile.model_name}${embeddingProfile.vector_dim ? ` · ${embeddingProfile.vector_dim} 维` : ""}` : "未启用 Embedding 模型")}</span><a href="/admin/models">查看模型配置</a></div>
            <label><span>目标知识库</span><select value={knowledgeBaseId} onChange={(event) => { setKnowledgeBaseId(event.target.value); setReviewed(false); }}>{knowledgeBases.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label><span>PDF 解析方式</span><select value={ocrMode} onChange={(event) => { setOcrMode(event.target.value); setReviewed(false); }}><option value="auto">自动判断</option><option value="force">强制 OCR</option><option value="native">仅使用文本层</option></select><small>自动模式优先版面解析，空页回退到 PDF 文本层。</small></label>
            <label><span>重复文件</span><select value={duplicateMode} onChange={(event) => { setDuplicateMode(event.target.value); setReviewed(false); }}><option value="skip">跳过相同 SHA-256</option><option value="replace">重新解析并替换</option></select></label>
          </section>

          <section className="intake-summary-card" aria-live="polite">
            <div><span>可导入</span><strong>{String(readyFiles.length).padStart(2, "0")}</strong></div>
            <div><span>需处理</span><strong>{String(blockedFiles.length).padStart(2, "0")}</strong></div>
            <div><span>合计大小</span><strong>{formatBytes(totalBytes)}</strong></div>
          </section>

          {reviewed ? <p className="intake-review-note" role="status">清单检查完成。确认目标知识库和解析设置后即可导入。</p> : null}
          {uploadError ? <p className="admin-error" role="alert">{uploadError}</p> : null}
          <button className="intake-review-button" type="button" disabled={!readyFiles.length || !knowledgeBaseId || uploading} onClick={() => setReviewed(true)}>检查导入清单</button>
          <button className="intake-review-button" type="button" disabled={!reviewed || !readyFiles.length || !knowledgeBaseId || !embeddingProfile || uploading || (ocrMode !== "native" && mineruStatus !== "reachable")} onClick={startImport}>{uploading ? "正在上传…" : "开始导入"}</button>
          <p className="intake-footnote">PDF 默认通过 MinerU 解析；纯文本层模式可跳过 MinerU。任务失败会保留上传文件供原任务重试；旧任务若未保留文件，请重新选择文件上传。</p>
        </aside>
      </section>
      <section className="intake-panel intake-jobs" aria-label="已入库文档">
        <div className="intake-panel-heading"><div><p className="section-kicker">INDEXED DOCUMENTS</p><h2>已入库文档 · {documents.length}</h2></div></div>
        <p className="intake-footnote">显示当前知识库、当前向量索引中的全部文件。重新向量化使用保存的切块；如需重新解析原文件，请重新上传。</p>
        {documentError ? <p className="admin-error" role="alert">{documentError}</p> : null}
        {!documents.length && !documentError ? <p className="intake-footnote">当前知识库尚无已索引文档。</p> : documents.map((item) => (
          <article className="intake-job-row" key={item.document_id}>
            <strong>{item.document_name}</strong>
            <span>{item.chunk_count} 个切块 · {item.page_count} 页 · {knowledgeBaseId}</span>
            <div className="intake-document-actions">
              <button type="button" disabled={!!documentBusy} onClick={() => void showDocument(item.document_id)}>查看详情</button>
              <button type="button" disabled={!!documentBusy} onClick={() => void reindexDocument(item)}>重新向量化</button>
              <button type="button" disabled={!!documentBusy} onClick={() => void removeDocument(item)}>删除文档</button>
            </div>
            {documentDetail?.document_id === item.document_id ? (
              <div className="intake-document-detail">{documentDetail.chunks.map((chunk, index) => (
                <details key={`${index}-${chunk.page_start}`}><summary>第 {chunk.page_start}–{chunk.page_end} 页 · {chunk.section_title || `切块 ${index + 1}`}</summary><pre>{chunk.content}</pre></details>
              ))}</div>
            ) : null}
          </article>
        ))}
      </section>
      <section className="intake-panel intake-jobs" aria-live="polite">
        <div className="intake-panel-heading"><div><p className="section-kicker">IMPORT HISTORY</p><h2>导入任务</h2></div></div>
        {!jobs.length ? <p className="intake-footnote">尚无导入任务。</p> : jobs.map((job) => (
          <article className="intake-job-row" key={job.id}>
            <strong>{job.file_name}</strong>
            <span>{({ queued: "等待处理", parsing: "正在解析", embedding: "正在向量化", completed: "已完成", failed: "失败", deleted: "已删除" } as const)[job.status]} · {job.completed_chunks}/{job.total_chunks || "?"} 切块 · {job.knowledge_base_id}</span>
            {job.error ? <p className="form-error">{job.error}</p> : null}
            {job.status === "failed" ? <button className="intake-retry-button" type="button" disabled={!!documentBusy} onClick={() => void retryImport(job)}>重试导入</button> : null}
          </article>
        ))}
      </section>
    </main>
  );
}
