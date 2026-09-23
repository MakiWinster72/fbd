"use client";

import { DragEvent, useEffect, useRef, useState } from "react";
import { getCurrentUser, listAdminKnowledgeBases, type KnowledgeBase } from "@/lib/api";

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
    return { id: fileId(file), file, status: "blocked", reason: extension === "doc" ? "旧版 DOC 请先转换为 DOCX 或 PDF" : "仅支持 PDF 和 DOCX" };
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

  useEffect(() => {
    getCurrentUser().then(async (user) => {
      if (!user) return window.location.replace("/login");
      if (user.role !== "admin") return window.location.replace("/");
      const bases = await listAdminKnowledgeBases();
      setKnowledgeBases(bases);
      setKnowledgeBaseId(bases[0]?.id ?? "");
      setReady(true);
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, []);

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
              <p>单个文件不超过 200 MB；旧版 DOC 需要先转换格式。</p>
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
            <label><span>目标知识库</span><select value={knowledgeBaseId} onChange={(event) => setKnowledgeBaseId(event.target.value)}>{knowledgeBases.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label><span>PDF 解析方式</span><select value={ocrMode} onChange={(event) => setOcrMode(event.target.value)}><option value="auto">自动判断</option><option value="force">强制 OCR</option><option value="native">仅使用文本层</option></select><small>自动模式优先版面解析，空页回退到 PDF 文本层。</small></label>
            <label><span>重复文件</span><select value={duplicateMode} onChange={(event) => setDuplicateMode(event.target.value)}><option value="skip">跳过相同 SHA-256</option><option value="replace">重新解析并替换</option></select></label>
          </section>

          <section className="intake-summary-card" aria-live="polite">
            <div><span>可导入</span><strong>{String(readyFiles.length).padStart(2, "0")}</strong></div>
            <div><span>需处理</span><strong>{String(blockedFiles.length).padStart(2, "0")}</strong></div>
            <div><span>合计大小</span><strong>{formatBytes(totalBytes)}</strong></div>
          </section>

          {reviewed ? <p className="intake-review-note" role="status">清单检查完成。下一阶段接入上传接口后，即可开始后台解析与向量化。</p> : null}
          <button className="intake-review-button" type="button" disabled={!readyFiles.length || !knowledgeBaseId} onClick={() => setReviewed(true)}>检查导入清单</button>
          <button className="intake-import-button" type="button" disabled title="上传与后台任务接口将在下一阶段接入">开始导入 · 接口待接入</button>
          <p className="intake-footnote">当前提交仅完成界面与前端校验，不会上传文件或修改知识库。</p>
        </aside>
      </section>
    </main>
  );
}
