"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useLanguage } from "@/components/language-provider";
import {
  activateChatProfile,
  activateEmbeddingProfile,
  activateRerankerProfile,
  deactivateRerankerProfile,
  createModelProfile,
  deleteModelProfile,
  getCurrentUser,
  getIndexJob,
  listModelProfiles,
  testModelProfile,
  updateModelProfile,
  type IndexJob,
  type ModelProfile,
  type ModelProfileInput,
} from "@/lib/api";

const EMPTY_FORM: ModelProfileInput = {
  name: "",
  purpose: "chat",
  protocol: "ollama",
  base_url: "http://127.0.0.1:11434",
  api_key: "",
  model_name: "",
  vector_dim: null,
  options: {},
};

function ProfileCard({ profile, onChanged, onJob }: { profile: ModelProfile; onChanged: () => Promise<void>; onJob: (job: IndexJob) => void }) {
  const { t } = useLanguage();
  const [name, setName] = useState(profile.name);
  const [baseUrl, setBaseUrl] = useState(profile.base_url);
  const [modelName, setModelName] = useState(profile.model_name);
  const [apiKey, setApiKey] = useState("");
  const [vectorDim, setVectorDim] = useState(profile.vector_dim?.toString() ?? "");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <article className={`model-profile-card${profile.is_active ? " active" : ""}`}>
      <header>
        <div>
          <span className="profile-purpose">{{ chat: "CHAT", embedding: "EMBEDDING", reranker: "RERANKER" }[profile.purpose]}</span>
          <h3>{profile.name}</h3>
        </div>
        {profile.is_active ? <strong className="active-badge">{t("当前启用")}</strong> : null}
      </header>
      <div className="profile-fields">
        <label><span>{t("配置名称")}</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label><span>{t("协议")}</span><input value={{ ollama: "Ollama", openai_compatible: "OpenAI Compatible", tei: "Hugging Face TEI", jina_compatible: "Jina/SiliconFlow Compatible", ollama_reranker: "Ollama Qwen3 Reranker" }[profile.protocol]} disabled /></label>
        <label className="wide"><span>Base URL</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
        <label className="wide"><span>{t("模型名称")}</span><input value={modelName} onChange={(event) => setModelName(event.target.value)} /></label>
        <label className="wide"><span>API Key ({t(profile.has_api_key ? "已保存，留空不修改" : "可选")})</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" /></label>
        {profile.purpose === "embedding" ? (
          <label><span>{t("向量维度")}</span><input inputMode="numeric" value={vectorDim} onChange={(event) => setVectorDim(event.target.value)} placeholder={t("测试后自动识别")} /></label>
        ) : null}
      </div>
      {notice ? <p className="form-notice">{t(notice)}</p> : null}
      {error ? <p className="form-error" role="alert">{t(error)}</p> : null}
      <footer className="profile-actions">
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() => run("save", async () => {
            await updateModelProfile(profile.id, {
              name,
              base_url: baseUrl,
              model_name: modelName,
              ...(apiKey ? { api_key: apiKey } : {}),
              ...(profile.purpose === "embedding" && vectorDim ? { vector_dim: Number(vectorDim) } : {}),
            });
            setApiKey("");
            setNotice("配置已保存");
            await onChanged();
          })}
        >{t(busy === "save" ? "保存中…" : "保存")}</button>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() => run("test", async () => {
            const result = await testModelProfile(profile.id);
            if (result.dimensions) setVectorDim(String(result.dimensions));
            setNotice(result.dimensions ? `${t("连接成功，向量维度")} ${result.dimensions}` : `${t("连接成功")}：${result.sample ?? t("模型已响应")}`);
            await onChanged();
          })}
        >{t(busy === "test" ? "测试中…" : "测试连接")}</button>
        {!profile.is_active && profile.purpose === "chat" ? (
          <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => run("activate", async () => {
            await activateChatProfile(profile.id);
            setNotice("已切换对话模型");
            await onChanged();
          })}>{t(busy === "activate" ? "切换中…" : "设为当前对话模型")}</button>
        ) : null}
        {!profile.is_active && profile.purpose === "embedding" ? (
          <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => {
            if (!window.confirm(t("更换 Embedding 会创建新 Milvus Collection 并重新生成全部向量。构建完成前仍使用旧索引，是否继续？"))) return;
            void run("activate", async () => {
              const job = await activateEmbeddingProfile(profile.id);
              onJob(job);
              setNotice("索引任务已启动，完成后自动切换");
            });
          }}>{t(busy === "activate" ? "正在启动…" : "重建索引并启用")}</button>
        ) : null}
        {!profile.is_active && profile.purpose === "reranker" ? (
          <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => run("activate", async () => {
            await activateRerankerProfile(profile.id);
            setNotice("已启用重排序，后续检索将先召回候选再重排");
            await onChanged();
          })}>{t(busy === "activate" ? "启用中…" : "启用重排序")}</button>
        ) : null}
        {profile.is_active && profile.purpose === "reranker" ? (
          <button type="button" disabled={Boolean(busy)} onClick={() => run("deactivate", async () => {
            await deactivateRerankerProfile();
            setNotice("已停用重排序，恢复向量检索顺序");
            await onChanged();
          })}>{t(busy === "deactivate" ? "停用中…" : "停用重排序")}</button>
        ) : null}
        {!profile.is_active ? (
          <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => {
            if (!window.confirm(`${t("确定删除")} “${profile.name}”?`) ) return;
            void run("delete", async () => { await deleteModelProfile(profile.id); await onChanged(); });
          }}>{t("删除")}</button>
        ) : null}
      </footer>
    </article>
  );
}

export default function AdminModelsPage() {
  const { t } = useLanguage();
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [form, setForm] = useState<ModelProfileInput>(EMPTY_FORM);
  const [ready, setReady] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<IndexJob | null>(null);

  const loadProfiles = useCallback(async () => {
    setProfiles(await listModelProfiles());
  }, []);

  useEffect(() => {
    getCurrentUser().then(async (user) => {
      if (!user) return window.location.replace("/login");
      if (user.role !== "admin") return window.location.replace("/");
      await loadProfiles();
      setReady(true);
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, [loadProfiles]);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await getIndexJob(job.id);
        setJob(next);
        if (next.status === "completed") await loadProfiles();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取索引进度");
      }
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [job, loadProfiles]);

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await createModelProfile({
        ...form,
        vector_dim: form.purpose === "embedding" && form.vector_dim ? Number(form.vector_dim) : null,
      });
      setForm(EMPTY_FORM);
      setShowCreate(false);
      await loadProfiles();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    }
  }

  if (!ready) return <main className="auth-status"><span className="auth-spinner" /><p>{t(error || "正在验证管理员权限…")}</p></main>;

  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div><p className="section-kicker">ADMIN CONTROL PLANE</p><h1>{t("模型配置中心")}</h1><p>{t("模型密钥仅保存在后端，普通用户无法查看或选择模型。")}</p></div>
        <nav><a href="/">{t("返回知识库")}</a><a href="/admin/documents">{t("文件导入")}</a><a href="/admin/users">{t("用户管理")}</a><button type="button" onClick={() => setShowCreate((value) => !value)}>{t(showCreate ? "取消新增" : "新增模型配置")}</button></nav>
      </header>

      {error ? <p className="admin-error" role="alert">{t(error)}</p> : null}
      {job ? (
        <section className={`index-job ${job.status}`}>
          <div><span>EMBEDDING INDEX</span><strong>{t(job.status === "completed" ? "新索引已启用" : job.status === "failed" ? "索引构建失败" : "正在构建新索引")}</strong></div>
          <p>{job.completed_chunks} / {job.total_chunks || "?"} {t("个文本块")}</p>
          {job.total_chunks ? <div className="job-progress"><span style={{ width: `${job.completed_chunks / job.total_chunks * 100}%` }} /></div> : null}
          {job.error ? <p className="form-error">{job.error}</p> : null}
        </section>
      ) : null}

      {showCreate ? (
        <form className="create-profile-form" onSubmit={createProfile}>
          <div><p className="section-kicker">NEW PROFILE</p><h2>{t("新增模型配置")}</h2></div>
          <label><span>{t("用途")}</span><select value={form.purpose} onChange={(event) => {
            const purpose = event.target.value as "chat" | "embedding" | "reranker";
            setForm({
              ...form,
              purpose,
              protocol: purpose === "reranker" ? "ollama_reranker" : "ollama",
              base_url: "http://127.0.0.1:11434",
              model_name: purpose === "reranker" ? "dengcao/Qwen3-Reranker-8B:Q4_K_M" : "",
              vector_dim: null,
            });
          }}><option value="chat">{t("对话模型")}</option><option value="embedding">{t("Embedding 模型")}</option><option value="reranker">{t("重排序模型")}</option></select></label>
          <label><span>{t("协议")}</span><select value={form.protocol} onChange={(event) => {
            const protocol = event.target.value as ModelProfileInput["protocol"];
            setForm({
              ...form,
              protocol,
              ...(protocol === "ollama_reranker" ? {
                base_url: "http://127.0.0.1:11434",
                model_name: "dengcao/Qwen3-Reranker-8B:Q4_K_M",
              } : {}),
            });
          }}>
            {form.purpose === "reranker" ? <><option value="ollama_reranker">Ollama Qwen3 Reranker ({t("本地")})</option><option value="jina_compatible">Jina/SiliconFlow Compatible</option><option value="tei">Hugging Face TEI</option></> : <><option value="ollama">Ollama</option><option value="openai_compatible">OpenAI Compatible</option></>}
          </select></label>
          <label><span>{t("配置名称")}</span><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label><span>Base URL</span><input required value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
          <label><span>{t("模型名称")}</span><input required value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} /></label>
          <label><span>{t("API Key（本地服务可留空）")}</span><input type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} autoComplete="new-password" /></label>
          {form.purpose === "embedding" ? <label><span>{t("向量维度（可稍后测试识别）")}</span><input inputMode="numeric" value={form.vector_dim ?? ""} onChange={(event) => setForm({ ...form, vector_dim: event.target.value ? Number(event.target.value) : null })} /></label> : null}
          <button className="primary" type="submit">{t("保存配置")}</button>
        </form>
      ) : null}

      <section className="profile-section"><div className="admin-section-heading"><p className="section-kicker">GENERATION</p><h2>{t("对话模型")}</h2></div>{profiles.filter((item) => item.purpose === "chat").map((profile) => <ProfileCard key={profile.id} profile={profile} onChanged={loadProfiles} onJob={setJob} />)}</section>
      <section className="profile-section"><div className="admin-section-heading"><p className="section-kicker">RETRIEVAL</p><h2>{t("Embedding 模型")}</h2><p>{t("切换时会在后台重建独立 Collection，成功后再原子切换。")}</p></div>{profiles.filter((item) => item.purpose === "embedding").map((profile) => <ProfileCard key={profile.id} profile={profile} onChanged={loadProfiles} onJob={setJob} />)}</section>
      <section className="profile-section"><div className="admin-section-heading"><p className="section-kicker">RERANKING</p><h2>{t("重排序模型")}</h2><p>{t("启用后先从 Milvus 召回候选，再按问题与文本的相关性重排。支持本地 Ollama Qwen3、TEI 和 Jina/SiliconFlow；8GB 显存环境会在重排后卸载本地模型，再加载对话模型。服务异常时自动降级到向量顺序。")}</p></div>{profiles.filter((item) => item.purpose === "reranker").map((profile) => <ProfileCard key={profile.id} profile={profile} onChanged={loadProfiles} onJob={setJob} />)}{profiles.every((item) => item.purpose !== "reranker") ? <p className="empty-profile-note">{t("尚未配置重排序模型。系统当前直接使用向量检索顺序。")}</p> : null}</section>
    </main>
  );
}
