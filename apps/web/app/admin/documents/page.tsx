"use client";

import { DragEvent, useEffect, useRef, useState } from "react";
import { AdminPage } from "../../../components/admin-page";
import { ImportPagination } from "../../../components/import-pagination";
import { paginate } from "../../../lib/pagination";
import "./documents.css";
import { useLanguage } from "@/components/language-provider";
import { deleteIndexedDocument, discardFailedUpload, getCurrentUser, getDocumentImport, getIndexedDocument, getMineruStatus, listAdminKnowledgeBases, listDocumentImports, listIndexedDocuments, listModelProfiles, reindexIndexedDocument, retryDocumentImport, uploadDocument, type DocumentImportJob, type IndexedDocument, type IndexedDocumentDetail, type KnowledgeBase, type ModelProfile } from "@/lib/api";

type IntakeStatus = "ready" | "blocked";
type JobFilter = "all" | "active" | "failed" | "completed" | "deleted" | "discarded";

type IntakeFile = {
  id: string;
  file: File;
  status: IntakeStatus;
  reason?: string;
};

const MAX_FILE_BYTES = 200 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = ["pdf", "docx", "md", "txt"];

function formatBytes(bytes: number) {
  if (bytes === 0) return "0 KB";
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
    return { id: fileId(file), file, status: "blocked", reason: extension === "doc" ? "旧版 DOC 请先转换为 DOCX 或 PDF" : "当前入库流程仅支持 PDF、DOCX、MD 和 TXT，请先转换格式" };
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
  const { t } = useLanguage();
  const inputRef = useRef<HTMLInputElement>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [files, setFiles] = useState<IntakeFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [ocrMode, setOcrMode] = useState("auto");
  const [duplicateMode, setDuplicateMode] = useState("skip");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [recordView, setRecordView] = useState<"documents" | "jobs">("documents");
  const [documentPage, setDocumentPage] = useState(1);
  const [jobPage, setJobPage] = useState(1);
  const [selectionNotice, setSelectionNotice] = useState("");
  const [mineruStatus, setMineruStatus] = useState<"checking" | "reachable" | "offline">("checking");
  const [embeddingProfile, setEmbeddingProfile] = useState<ModelProfile | null>(null);
  const [embeddingError, setEmbeddingError] = useState("");
  const [jobs, setJobs] = useState<DocumentImportJob[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [jobActionError, setJobActionError] = useState("");
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [documentDetail, setDocumentDetail] = useState<IndexedDocumentDetail | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [documentBusy, setDocumentBusy] = useState("");
  const [documentRevision, setDocumentRevision] = useState(0);
  const [jobFilter, setJobFilter] = useState<JobFilter>("all");
  const [jobKnowledgeBase, setJobKnowledgeBase] = useState("all");
  const [jobSearch, setJobSearch] = useState("");
  const [jobsRefreshing, setJobsRefreshing] = useState(false);

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
    setDocumentPage(1);
    listIndexedDocuments(knowledgeBaseId).then((items) => {
      if (!cancelled) { setDocuments(items); setDocumentError(""); }
    }).catch((reason) => { if (!cancelled) setDocumentError(reason instanceof Error ? reason.message : "无法加载文档清单"); });
    return () => { cancelled = true; };
  }, [knowledgeBaseId, documentRevision, jobs.filter((job) => job.status === "completed" || job.status === "deleted").map((job) => `${job.id}:${job.status}`).join(",")]);

  useEffect(() => {
    const active = jobs.filter((job) => ["queued", "parsing", "embedding"].includes(job.status));
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
    if (!window.confirm(`${t("确认从当前知识库删除")}《${item.document_name}》？${t("此操作会清除索引和可重建的切块，无法撤销。")}`)) return;
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
      setRecordView("jobs");
      setJobPage(1);
      setJobFilter("all");
      setJobKnowledgeBase("all");
      setJobSearch("");
      setSelectionNotice(`《${item.document_name}》${t("已提交重新向量化任务，请查看下方进度。")}`);
    } catch (reason) { setDocumentError(reason instanceof Error ? reason.message : "重新向量化失败"); }
    finally { setDocumentBusy(""); }
  }

  async function retryImport(job: DocumentImportJob) {
    setDocumentBusy(job.id);
    setJobActionError("");
    try {
      const updated = await retryDocumentImport(job.id);
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setJobActionError(`${job.file_name}：${reason instanceof Error ? reason.message : "重试失败"}`);
    } finally { setDocumentBusy(""); }
  }

  async function discardUpload(job: DocumentImportJob) {
    if (!window.confirm(`${t("确认清理")}《${job.file_name}》${t("的失败上传原件？清理后无法重试该任务，但不会删除已索引文档。")}`)) return;
    setDocumentBusy(job.id);
    setJobActionError("");
    try {
      const updated = await discardFailedUpload(job.id);
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setJobActionError(`${job.file_name}：${reason instanceof Error ? reason.message : "清理失败"}`);
    } finally { setDocumentBusy(""); }
  }

  async function refreshJobs() {
    setJobsRefreshing(true);
    try { setJobs(await listDocumentImports()); setJobActionError(""); }
    catch (reason) { setJobActionError(reason instanceof Error ? reason.message : "刷新任务失败"); }
    finally { setJobsRefreshing(false); }
  }

  async function startImport() {
    if (!readyFiles.length || !knowledgeBaseId || uploading) return;
    setUploading(true);
    setRecordView("jobs");
    setJobPage(1);
    setJobFilter("all");
    setJobKnowledgeBase("all");
    setJobSearch("");
    setUploadError("");
    const succeeded = new Set<string>();
    try {
      for (const item of readyFiles) {
        try {
          const job = await uploadDocument(item.file, knowledgeBaseId, ocrMode, duplicateMode);
          setJobs((current) => [job, ...current.filter((other) => other.id !== job.id)]);
          succeeded.add(item.id);
        } catch (reason) {
          setUploadError(`${item.file.name}：${t(reason instanceof Error ? reason.message : "上传失败")}。${t("其余文件将继续上传。")}`);
        }
      }
      setFiles((current) => current.filter((item) => !succeeded.has(item.id)));
      setSelectionNotice(succeeded.size ? `${t("已提交")} ${succeeded.size} ${t("个文件，后台任务进度见下方。")}` : t("上传失败，请检查提示后重试。"));
    } finally {
      setUploading(false);
    }
  }

  function addFiles(selected: FileList | File[]) {
    const chosen = Array.from(selected);
    if (!chosen.length) return;
    const known = new Set(files.map((item) => item.id));
    const additions = chosen.map(classify).filter((item) => {
      if (known.has(item.id)) return false;
      known.add(item.id);
      return true;
    });
    setSelectionNotice(`${t("已选择")} ${additions.length} ${t("个文件")}${chosen.length > additions.length ? `${t("，跳过")} ${chosen.length - additions.length} ${t("个重复项")}` : ""}`);
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
  const visibleJobs = jobs.filter((job) => {
    if (jobKnowledgeBase !== "all" && job.knowledge_base_id !== jobKnowledgeBase) return false;
    if (jobSearch.trim() && !job.file_name.toLocaleLowerCase().includes(jobSearch.trim().toLocaleLowerCase())) return false;
    if (jobFilter === "active") return ["queued", "parsing", "embedding"].includes(job.status);
    return jobFilter === "all" || job.status === jobFilter;
  });

  const documentResults = paginate(documents, documentPage);
  const jobResults = paginate(visibleJobs, jobPage);
  const needsParser = readyFiles.some((item) => extensionOf(item.file.name) === "pdf") && ocrMode !== "native";
  const importBlocker = !knowledgeBases.length ? "请先创建知识库" : embeddingError || (!embeddingProfile ? "请先启用 Embedding 模型" : needsParser && mineruStatus !== "reachable" ? mineruStatus === "checking" ? "正在检查 PDF 解析服务…" : "PDF 解析服务不可用，可在高级设置中选择仅使用文本层" : "");

  if (!ready) return <main id="main-content" tabIndex={-1} className="auth-status"><span className="auth-spinner" /><p>{t(error || "正在验证管理员权限…")}</p></main>;

  return (
    <AdminPage title={t("文件导入")} className="document-intake-shell">

      {error ? <p className="admin-error" role="alert">{t(error)}</p> : null}

      <section className="intake-layout">
        <div className="intake-main-column">
          <section className="intake-panel">
            {selectionNotice ? <p className="intake-selection-notice" role="status">{selectionNotice}</p> : null}
            <div
              className={`intake-dropzone${dragging ? " dragging" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
              onDrop={onDrop}
            >
              <svg viewBox="0 0 48 48" aria-hidden="true"><path d="M14 5h14l8 8v30H14z" /><path d="M28 5v9h8M24 34V20m-6 6 6-6 6 6" /></svg>
              <h3>{t("拖放文件到这里")}</h3>
              <p>{t("PDF、DOCX、MD、TXT · 每个文件不超过 200 MB")}</p>
              <button type="button" onClick={() => inputRef.current?.click()}>{t("从电脑选择文件")}</button>
              <input
                className="visually-hidden"
                ref={inputRef}
                type="file"
                accept=".pdf,.docx,.md,.txt"
                multiple
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>
          </section>

          {files.length > 0 ? <section className="intake-panel intake-queue-panel">
            <div className="intake-panel-heading">
              <div><h2>{t("待导入")} · {files.length}</h2></div>
              <button className="intake-text-button" type="button" onClick={() => { setFiles([]); setSelectionNotice(""); }}>{t("清空清单")}</button>
            </div>

              <div className="intake-file-list" tabIndex={0} aria-label={t("待导入文件清单")}>
                {files.map((item) => (
                  <article className={`intake-file-row ${item.status}`} key={item.id}>
                    <div className="intake-file-type">{extensionOf(item.file.name).toUpperCase() || "FILE"}</div>
                    <div className="intake-file-copy">
                      <strong>{item.file.name}</strong>
                      <span>{formatBytes(item.file.size)}{item.reason ? ` · ${t(item.reason)}` : ""}</span>
                    </div>
                    <span className={`intake-status-mark ${item.status}`}>{t(item.status === "ready" ? "可导入" : "需处理")}</span>
                    <button className="intake-remove" type="button" aria-label={`${t("移除")} ${item.file.name}`} onClick={() => { setFiles((current) => current.filter((file) => file.id !== item.id)); }}>
                      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
                    </button>
                  </article>
                ))}
              </div>
          </section> : null}
        </div>

        <aside className="intake-sidebar">
          <section className="intake-config-card">
            <label><span>{t("目标知识库")}</span><select disabled={uploading} value={knowledgeBaseId} onChange={(event) => setKnowledgeBaseId(event.target.value)}>{knowledgeBases.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <details className="intake-advanced"><summary>{t("高级设置")}</summary>
            <label><span>{t("PDF 解析方式")}</span><select value={ocrMode} disabled={uploading} onChange={(event) => setOcrMode(event.target.value)}><option value="auto">{t("自动判断")}</option><option value="force">{t("强制 OCR")}</option><option value="native">{t("仅使用文本层")}</option></select><small>{t("自动模式优先版面解析，空页回退到 PDF 文本层。")}</small></label>
            <label><span>{t("重复文件")}</span><select value={duplicateMode} disabled={uploading} onChange={(event) => setDuplicateMode(event.target.value)}><option value="skip">{t("跳过相同文件")}</option><option value="replace">{t("重新解析并替换")}</option></select></label>
            <p className="intake-footnote">{t("当前 Embedding")}：{embeddingProfile?.name ?? t("未启用")} · <a href="/admin/models">{t("查看模型配置")}</a></p>
            </details>
          </section>

          <section hidden={!files.length} className="intake-summary-card" aria-live="polite">
            <div><span>{t("可导入")}</span><strong>{String(readyFiles.length).padStart(2, "0")}</strong></div>
            <div><span>{t("需处理")}</span><strong>{String(blockedFiles.length).padStart(2, "0")}</strong></div>
            <div><span>{t("合计大小")}</span><strong>{formatBytes(totalBytes)}</strong></div>
          </section>

          {uploadError ? <p className="admin-error" role="alert">{t(uploadError)}</p> : null}
          {importBlocker ? <p className="intake-footnote" id="import-blocker" role="status">{t(importBlocker)}</p> : null}
          <button className="intake-review-button" type="button" aria-describedby={importBlocker ? "import-blocker" : undefined} disabled={!readyFiles.length || !knowledgeBaseId || !!importBlocker || uploading} onClick={startImport}>{t(uploading ? "正在上传…" : "开始导入")}{readyFiles.length > 0 && !uploading ? ` (${readyFiles.length})` : ""}</button>
        </aside>
      </section>
      <section className="intake-records">
      <nav className="intake-record-tabs" aria-label={t("文档与任务")}>
        <button type="button" aria-pressed={recordView === "documents"} onClick={() => setRecordView("documents")}>{t("已入库文档")} <span>{documents.length}</span></button>
        <button type="button" aria-pressed={recordView === "jobs"} onClick={() => setRecordView("jobs")}>{t("导入任务")} <span>{jobs.length}</span></button>
      </nav>
      <section hidden={recordView !== "documents"} className="intake-panel intake-jobs" aria-label={t("已入库文档")}>

        {documentError ? <p className="admin-error" role="alert">{t(documentError)}</p> : null}
        {!documents.length && !documentError ? <p className="intake-footnote">{t("当前知识库尚无已索引文档。")}</p> : documentResults.items.map((item) => (
          <article className="intake-job-row" key={item.document_id}>
            <strong>{item.document_name}</strong>
            <span>{item.chunk_count} {t("个切块")} · {item.page_count} {t("页")}</span>
            <div className="intake-document-actions">
              <button type="button" disabled={!!documentBusy} onClick={() => void showDocument(item.document_id)}>{t("查看详情")}</button>
              <button type="button" disabled={!!documentBusy} onClick={() => void reindexDocument(item)}>{t("重新向量化")}</button>
              <button type="button" disabled={!!documentBusy} onClick={() => void removeDocument(item)}>{t("删除文档")}</button>
            </div>
            {documentDetail?.document_id === item.document_id ? (
              <div className="intake-document-detail">{documentDetail.chunks.map((chunk, index) => (
                <details key={`${index}-${chunk.page_start}`}><summary>{t("第")} {chunk.page_start}–{chunk.page_end} {t("页")} · {chunk.section_title || `${t("切块")} ${index + 1}`}</summary><pre>{chunk.content}</pre></details>
              ))}</div>
            ) : null}
          </article>
        ))}
        <ImportPagination page={documentResults.page} pages={documentResults.pages} total={documents.length} onChange={(page) => { setDocumentPage(page); setDocumentDetail(null); }} />
      </section>
      <section hidden={recordView !== "jobs"} className="intake-panel intake-jobs" aria-label={t("导入任务")}>
        <div className="intake-panel-heading"><span className="intake-footnote">{t("最近 50 条记录")}</span><button className="intake-text-button" type="button" disabled={jobsRefreshing} onClick={() => void refreshJobs()}>{t(jobsRefreshing ? "正在刷新…" : "刷新任务")}</button></div>
        {jobActionError ? <p className="admin-error" role="alert">{t(jobActionError)}</p> : null}
        <div className="intake-job-filters">
          <label>{t("状态")}<select value={jobFilter} onChange={(event) => { setJobFilter(event.target.value as JobFilter); setJobPage(1); }}><option value="all">{t("全部状态")}</option><option value="active">{t("进行中")}</option><option value="failed">{t("失败")}</option><option value="completed">{t("已完成")}</option><option value="deleted">{t("已删除文档")}</option><option value="discarded">{t("已清理原件")}</option></select></label>
          <label>{t("知识库")}<select value={jobKnowledgeBase} onChange={(event) => { setJobKnowledgeBase(event.target.value); setJobPage(1); }}><option value="all">{t("全部知识库")}</option>{knowledgeBases.map((base) => <option key={base.id} value={base.id}>{base.name}</option>)}</select></label>
          <label>{t("文件名")}<input type="search" value={jobSearch} onChange={(event) => { setJobSearch(event.target.value); setJobPage(1); }} placeholder={t("查找导入任务")} /></label>
        </div>
        {!jobs.length ? <p className="intake-footnote">{t("尚无导入任务。上传文件后将在这里看到处理进度。")}</p> : !visibleJobs.length ? <p className="intake-footnote">{t("没有符合筛选条件的任务，请调整状态、知识库或文件名。")}</p> : jobResults.items.map((job) => (
          <article className="intake-job-row" key={job.id}>
            <strong>{job.file_name}</strong>
            <span className={`intake-job-state ${job.status}`}>{t(({ queued: "等待处理", parsing: "正在解析", embedding: "正在向量化", completed: "已完成", failed: "失败", deleted: "已删除文档", discarded: "已清理原件" } as const)[job.status])} · {knowledgeBases.find((base) => base.id === job.knowledge_base_id)?.name ?? job.knowledge_base_id}</span>
            <span>{job.status === "embedding" ? `${job.completed_chunks}/${job.total_chunks || "?"} ${t("个切块已生成向量")}${job.total_chunks > 0 && job.completed_chunks >= job.total_chunks ? t("，等待写入索引") : ""}` : job.status === "completed" ? `${t("已入库")} ${job.completed_chunks} ${t("个切块")}` : job.status === "failed" ? `${t("已处理")} ${job.completed_chunks}/${job.total_chunks || "?"} ${t("个切块")}` : t(job.status === "parsing" ? "正在解析文件内容" : job.status === "queued" ? "已接收文件，等待后台处理" : job.status === "discarded" ? "失败原件已清理，无法再重试" : "索引已从知识库中删除")}</span>
            {job.error && job.status === "failed" ? <p className="form-error">{t("失败原因")}：{t(job.error)}</p> : null}
            {job.status === "failed" && !job.source_available ? <p className="intake-footnote">{t("未保留原文件；请重新选择文件上传。如为重新向量化任务，可从已入库文档重新发起。")}</p> : null}
            {job.status === "failed" && job.source_available ? <div className="intake-job-actions"><button type="button" disabled={!!documentBusy} onClick={() => void retryImport(job)}>{t("重试导入")}</button><button type="button" disabled={!!documentBusy} onClick={() => void discardUpload(job)}>{t("清理失败原件")}</button></div> : null}
          </article>
        ))}
        <ImportPagination page={jobResults.page} pages={jobResults.pages} total={visibleJobs.length} onChange={setJobPage} />
      </section>
      </section>
    </AdminPage>
  );
}
