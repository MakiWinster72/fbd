"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { askKnowledgeBase, fetchHealth, logout, type CurrentUser, type KnowledgeBase, type Source } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  grounded?: boolean;
};

type ServiceState = "checking" | "online" | "degraded" | "offline";

const INITIAL_MESSAGE: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "请询问已入库文档中的事实、制度或流程。我会严格依据资料回答，并标出原始文件和页码。",
};

const QUICK_QUESTIONS = [
  "这批资料的核心内容是什么？",
  "文档中有哪些重要时间节点？",
  "相关制度规定了哪些流程？",
];

function pageLabel(source: Source) {
  return source.page_start === source.page_end
    ? `PAGE ${String(source.page_start).padStart(2, "0")}`
    : `PAGE ${String(source.page_start).padStart(2, "0")}—${String(source.page_end).padStart(2, "0")}`;
}

function InlineText({
  text,
  sources,
  onCitation,
}: {
  text: string;
  sources: Source[];
  onCitation: (index: number) => void;
}) {
  const { t } = useLanguage();
  const sourceIndexes = new Set(sources.map((source) => source.index));
  return text.split(/(【\d+】|\*\*[^*]+\*\*)/g).map((part, index) => {
    const citationMatch = part.match(/^【(\d+)】$/);
    if (citationMatch) {
      const citation = Number(citationMatch[1]);
      if (!sourceIndexes.has(citation)) return null;
      return (
        <button
          className="inline-citation"
          key={`${part}-${index}`}
          type="button"
          aria-label={`${t("查看来源")} ${citation}`}
          onClick={() => onCitation(citation)}
        >
          {String(citation).padStart(2, "0")}
        </button>
      );
    }
    const boldMatch = part.match(/^\*\*([^*]+)\*\*$/);
    if (boldMatch) return <strong key={`${part}-${index}`}>{boldMatch[1]}</strong>;
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function AnswerContent({ message, onCitation }: { message: Message; onCitation: (index: number) => void }) {
  const sources = message.sources ?? [];
  const lines = message.content.split("\n");
  return (
    <div className="answer-copy">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div className="copy-gap" key={`gap-${index}`} />;
        const bullet = trimmed.match(/^(?:\*|-|•)\s+(.*)$/);
        if (bullet) {
          return (
            <div className="answer-bullet" key={`line-${index}`}>
              <span aria-hidden="true" />
              <p><InlineText text={bullet[1]} sources={sources} onCitation={onCitation} /></p>
            </div>
          );
        }
        return <p key={`line-${index}`}><InlineText text={trimmed} sources={sources} onCitation={onCitation} /></p>;
      })}
    </div>
  );
}

function SourceCards({
  sources,
  idPrefix,
  activeCitation,
}: {
  sources: Source[];
  idPrefix: string;
  activeCitation?: number | null;
}) {
  const { t } = useLanguage();
  return sources.map((source) => (
    <article
      className={`source-card${activeCitation === source.index ? " active" : ""}`}
      id={`${idPrefix}-${source.index}`}
      key={`${idPrefix}-${source.document_id}-${source.index}`}
    >
      <div className="source-index">{String(source.index).padStart(2, "0")}</div>
      <div className="source-card-body">
        <div className="source-meta">
          <span>{pageLabel(source)}</span>
          <span>{source.rerank_score !== null ? `${Math.round(source.rerank_score * 100)}% RERANK · ${Math.round(source.vector_score * 100)}% VECTOR` : `${Math.round(source.score * 100)}% MATCH`}</span>
        </div>
        <h3>{source.document_name}</h3>
        {source.section_title ? <p className="source-section">{source.section_title}</p> : null}
        <p className="source-excerpt">{source.excerpt}</p>
        <div className="confidence-track" aria-label={`${t("相似度")} ${Math.round(source.score * 100)}%`}>
          <span style={{ width: `${Math.max(0, Math.min(100, source.score * 100))}%` }} />
        </div>
      </div>
    </article>
  ));
}

export function ChatWorkspace({ currentUser, knowledgeBases }: { currentUser: CurrentUser; knowledgeBases: KnowledgeBase[] }) {
  const { t, locale } = useLanguage();
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState(knowledgeBases[0].id);
  const [messages, setMessages] = useState<Message[]>([INITIAL_MESSAGE]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serviceState, setServiceState] = useState<ServiceState>("checking");
  const [latestSources, setLatestSources] = useState<Source[]>([]);
  const [mobileEvidenceOpen, setMobileEvidenceOpen] = useState(false);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHealth()
      .then((health) => setServiceState(health.status === "ok" ? "online" : "degraded"))
      .catch(() => setServiceState("offline"));
  }, []);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!mobileEvidenceOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setMobileEvidenceOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    requestAnimationFrame(() => {
      document.getElementById(`mobile-source-${activeCitation ?? 1}`)?.scrollIntoView({ block: "nearest" });
    });
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [mobileEvidenceOpen, activeCitation]);

  function openCitation(index: number) {
    if (window.matchMedia("(max-width: 760px)").matches) {
      setActiveCitation(index);
      setMobileEvidenceOpen(true);
      return;
    }
    document.getElementById(`source-${index}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function changeKnowledgeBase(knowledgeBaseId: string) {
    setSelectedKnowledgeBaseId(knowledgeBaseId);
    setMessages([INITIAL_MESSAGE]);
    setLatestSources([]);
    setError(null);
  }

  async function submitQuestion(nextQuestion = question) {
    const value = nextQuestion.trim();
    if (!value || loading) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: value,
    };
    setQuestion("");
    setError(null);
    setMessages((current) => [...current, userMessage]);
    setLoading(true);

    try {
      const response = await askKnowledgeBase(value, selectedKnowledgeBaseId, locale);
      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.answer,
        sources: response.sources,
        grounded: response.grounded,
      };
      setMessages((current) => [...current, assistantMessage]);
      setLatestSources(response.sources);
    } catch (caught) {
      setError(t(caught instanceof Error ? caught.message : "问答服务暂时不可用"));
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submitQuestion();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  const statusLabel = t({
    checking: "检查服务",
    online: "检索服务在线",
    degraded: "部分服务异常",
    offline: "服务未连接",
  }[serviceState]);

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-seal" aria-hidden="true">知</div>
          <div>
            <p className="eyebrow">KNOWLEDGE RAG · PRIVATE ARCHIVE</p>
            <h1>Knowledge RAG</h1>
          </div>
        </div>
        <div className="topbar-meta">
          <div className={`service-pill ${serviceState}`}>
            <span aria-hidden="true" />
            {statusLabel}
          </div>
          <div className="collection-label">
            <span>{t("当前资料库")}</span>
            {knowledgeBases.length > 1 ? (
              <select
                aria-label={t("选择知识库")}
                value={selectedKnowledgeBaseId}
                onChange={(event) => changeKnowledgeBase(event.target.value)}
              >
                {knowledgeBases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            ) : <strong>{knowledgeBases[0].name}</strong>}
          </div>
          <div className="user-menu">
            <span>{currentUser.display_name}</span>
            {currentUser.role === "admin" ? <><a href="/admin/documents">{t("文件导入")}</a><a href="/admin/models">{t("管理后台")}</a></> : null}
            <button
              type="button"
              onClick={async () => {
                await logout();
                window.location.assign("/login");
              }}
            >
              {t("退出")}
            </button>
          </div>
        </div>
      </header>

      <section className="workspace-grid">
        <section className="conversation-panel" aria-label={t("知识库对话")}>
          <div className="conversation-heading">
            <div>
              <p className="section-kicker">ASK THE ARCHIVE</p>
              <h2>{t("从材料中寻找答案")}</h2>
            </div>
            <p className="conversation-note">{t("回答仅来自已入库文档，引用编号可定位到右侧证据。")}</p>
            {knowledgeBases.length > 1 ? (
              <label className="mobile-kb-selector">
                <span>{t("当前知识库")}</span>
                <select value={selectedKnowledgeBaseId} onChange={(event) => changeKnowledgeBase(event.target.value)}>
                  {knowledgeBases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            ) : null}
          </div>

          <div className="transcript" ref={transcriptRef} aria-live="polite">
            {messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <div className="message-label">
                  {t(message.role === "assistant" ? "资料助手" : "你的问题")}
                </div>
                <div className="message-body">
                  {message.role === "assistant" ? (
                    <AnswerContent message={message.id === "welcome" ? { ...message, content: t(message.content) } : message} onCitation={openCitation} />
                  ) : (
                    <p>{message.content}</p>
                  )}
                  {message.role === "assistant" && message.sources?.length ? (
                    <div className="message-evidence-line">
                      <span>{message.sources.length} {t("条证据")}</span>
                      <span>{t("已核对文件与页码")}</span>
                    </div>
                  ) : null}
                </div>
              </article>
            ))}

            {loading ? (
              <article className="message assistant loading-message">
                <div className="message-label">{t("资料助手")}</div>
                <div className="message-body">
                  <div className="search-progress">
                    <span /><span /><span />
                    <p>{t("正在检索材料并核对引用…")}</p>
                  </div>
                </div>
              </article>
            ) : null}
          </div>

          <div className="composer-zone">
            {latestSources.length ? (
              <button
                className="mobile-evidence-bar"
                type="button"
                onClick={() => {
                  setActiveCitation(latestSources[0]?.index ?? 1);
                  setMobileEvidenceOpen(true);
                }}
              >
                <span>{t("引用来源")} · {String(latestSources.length).padStart(2, "0")}</span>
                <strong>{t("查看页码与原文")}</strong>
              </button>
            ) : null}
            {messages.length === 1 ? (
              <div className="quick-questions" aria-label={t("示例问题")}>
                {QUICK_QUESTIONS.map((item) => (
                  <button type="button" key={item} onClick={() => void submitQuestion(t(item))}>{t(item)}</button>
                ))}
              </div>
            ) : null}
            {error ? (
              <div className="error-banner" role="alert">
                <strong>{t("本次请求未完成")}</strong>
                <span>{t(error)}。{t("请检查管理后台当前启用的对话模型协议、接口地址及连接测试结果；重排序失败会自动降级，不会阻止回答。")}</span>
              </div>
            ) : null}
            <form className="composer" onSubmit={onSubmit}>
              <label htmlFor="question">{t("向资料库提问")}</label>
              <div className="composer-row">
                <textarea
                  id="question"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={t("例如：相关制度规定了哪些流程？")}
                  rows={2}
                  maxLength={2000}
                  disabled={loading}
                />
                <button className="send-button" type="submit" disabled={!question.trim() || loading}>
                  <span>{t(loading ? "检索中" : "发送问题")}</span>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6" /></svg>
                </button>
              </div>
              <p className="composer-hint">{t("Enter 发送 · Shift + Enter 换行")}</p>
            </form>
          </div>
        </section>

        <aside className="evidence-panel" aria-label={t("引用来源")}>
          <div className="evidence-heading">
            <div>
              <p className="section-kicker">EVIDENCE LEDGER</p>
              <h2>{t("引用来源")}</h2>
            </div>
            <span className="source-count">{String(latestSources.length).padStart(2, "0")}</span>
          </div>

          <div className="evidence-list">
            {latestSources.length ? (
              <SourceCards sources={latestSources} idPrefix="source" activeCitation={activeCitation} />
            ) : (
              <div className="evidence-empty">
                <div className="empty-page" aria-hidden="true"><span>01</span></div>
                <h3>{t("答案的依据会显示在这里")}</h3>
                <p>{t("提问后可查看文件名、页码、相关度和命中的原文片段。")}</p>
              </div>
            )}
          </div>

          <div className="evidence-footnote">
            <span aria-hidden="true">※</span>
            <p>{t("引用内容来自自动解析结果。金额、人名和设备型号等关键信息应结合原始页面复核。")}</p>
          </div>
        </aside>
      </section>

      {mobileEvidenceOpen ? (
        <div className="mobile-evidence-backdrop" onClick={() => setMobileEvidenceOpen(false)}>
          <section
            className="mobile-evidence-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={t("引用来源")}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mobile-sheet-handle" aria-hidden="true" />
            <header className="mobile-sheet-header">
              <div>
                <p className="section-kicker">EVIDENCE LEDGER</p>
                <h2>{t("引用来源")} · {String(latestSources.length).padStart(2, "0")}</h2>
              </div>
              <button type="button" onClick={() => setMobileEvidenceOpen(false)} aria-label={t("关闭引用来源")}>{t("关闭")}</button>
            </header>
            <div className="mobile-sheet-list">
              <SourceCards sources={latestSources} idPrefix="mobile-source" activeCitation={activeCitation} />
            </div>
            <div className="evidence-footnote mobile-sheet-footnote">
              <span aria-hidden="true">※</span>
              <p>{t("金额、人名和设备型号等关键信息应结合原始页面复核。")}</p>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
