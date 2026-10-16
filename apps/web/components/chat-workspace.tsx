"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  askKnowledgeBase,
  fetchHealth,
  type CurrentUser,
  type KnowledgeBase,
  type Source,
} from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { useConversationHistory } from "@/components/use-conversation-history";
import { Modal } from "@/components/modal";
import { WorkspaceNavigation } from "@/components/workspace-navigation";
import { useWorkspaceViewport } from "@/components/use-workspace-viewport";

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
    ? String(source.page_start).padStart(2, "0")
    : `${String(source.page_start).padStart(2, "0")}—${String(source.page_end).padStart(2, "0")}`;
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
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
    if (boldMatch)
      return <strong key={`${part}-${index}`}>{boldMatch[1]}</strong>;
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function AnswerContent({
  message,
  onCitation,
}: {
  message: Message;
  onCitation: (index: number) => void;
}) {
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
              <p>
                <InlineText
                  text={bullet[1]}
                  sources={sources}
                  onCitation={onCitation}
                />
              </p>
            </div>
          );
        }
        return (
          <p key={`line-${index}`}>
            <InlineText
              text={trimmed}
              sources={sources}
              onCitation={onCitation}
            />
          </p>
        );
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
      tabIndex={-1}
      key={`${idPrefix}-${source.document_id}-${source.index}`}
    >
      <div className="source-index">
        {String(source.index).padStart(2, "0")}
      </div>
      <div className="source-card-body">
        <div className="source-meta">
          <span>
            {t("页码")} {pageLabel(source)}
          </span>
          <span>
            {source.rerank_score !== null
              ? `${t("重排序得分")} ${source.rerank_score.toFixed(2)}`
              : `${t("向量得分")} ${source.vector_score.toFixed(2)}`}
          </span>
        </div>
        <h3>{source.document_name}</h3>
        {source.section_title ? (
          <p className="source-section">{source.section_title}</p>
        ) : null}
        <details className="source-passage">
          <summary>{t("查看完整引用片段")}</summary>
          <p className="source-full-excerpt">{source.excerpt}</p>
        </details>
        <p className="source-excerpt">{source.excerpt}</p>
        <div
          className="confidence-track"
          aria-label={`${t("相似度")} ${Math.round(source.score * 100)}%`}
        >
          <span
            style={{
              width: `${Math.max(0, Math.min(100, source.score * 100))}%`,
            }}
          />
        </div>
      </div>
    </article>
  ));
}

export function ChatWorkspace({
  currentUser,
  knowledgeBases,
}: {
  currentUser: CurrentUser;
  knowledgeBases: KnowledgeBase[];
}) {
  const { t, locale } = useLanguage();
  const [messages, setMessages] = useState<Message[]>([INITIAL_MESSAGE]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serviceState, setServiceState] = useState<ServiceState>("checking");
  const [latestSources, setLatestSources] = useState<Source[]>([]);
  const [mobileEvidenceOpen, setMobileEvidenceOpen] = useState(false);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const mainScrollRef = useRef<HTMLDivElement>(null);
  const evidenceListRef = useRef<HTMLDivElement>(null);
  useWorkspaceViewport(workspaceRef);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const followTranscript = useRef(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1001px)");
    const closeOnDesktop = () => {
      if (desktop.matches) setNavigationOpen(false);
    };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);
  const history = useConversationHistory(
    currentUser.id,
    knowledgeBases,
    loading,
  );
  const selectedKnowledgeBaseId = history.knowledgeBaseId;
  const busy = loading || history.restoring;

  useEffect(() => {
    const detail = history.replay.detail;
    followTranscript.current = true;
    setMessages([
      INITIAL_MESSAGE,
      ...(detail?.turns.flatMap((turn): Message[] => [
        { id: `turn-${turn.id}-user`, role: "user", content: turn.question },
        {
          id: `turn-${turn.id}-assistant`,
          role: "assistant",
          content: turn.answer,
          sources: turn.sources,
          grounded: turn.grounded,
        },
      ]) ?? []),
    ]);
    setLatestSources(detail?.turns.at(-1)?.sources ?? []);
    setActiveCitation(null);
    setMobileEvidenceOpen(false);
    setQuestion("");
    setError(null);
    mainScrollRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [history.replay]);

  useEffect(() => {
    fetchHealth()
      .then((health) =>
        setServiceState(health.status === "ok" ? "online" : "degraded"),
      )
      .catch(() => setServiceState("offline"));
  }, []);

  useEffect(() => {
    if (followTranscript.current)
      transcriptRef.current?.scrollTo({
        top: transcriptRef.current.scrollHeight,
        behavior: history.restoring ? "auto" : scrollBehavior(),
      });
  }, [messages, loading]);

  useEffect(() => {
    if (!mobileEvidenceOpen) return;
    const frame = requestAnimationFrame(() => {
      document
        .getElementById(`mobile-source-${activeCitation ?? 1}`)
        ?.scrollIntoView({ block: "nearest" });
    });
    return () => cancelAnimationFrame(frame);
  }, [mobileEvidenceOpen, activeCitation]);

  function openCitation(index: number, sources: Source[]) {
    setLatestSources(sources);
    setActiveCitation(index);
    if (window.matchMedia("(max-width: 760px)").matches) {
      setMobileEvidenceOpen(true);
      return;
    }
    requestAnimationFrame(() => {
      const source = document.getElementById(`source-${index}`);
      source?.focus({ preventScroll: true });
      showEvidence();
      const list = evidenceListRef.current;
      if (source && list)
        list.scrollTo({
          top: source.offsetTop - list.offsetTop,
          behavior: scrollBehavior(),
        });
    });
  }

  function showEvidence() {
    const main = mainScrollRef.current;
    if (main)
      main.scrollTo({ top: main.clientHeight, behavior: scrollBehavior() });
  }

  function returnToChat() {
    mainScrollRef.current?.scrollTo({ top: 0, behavior: scrollBehavior() });
    questionRef.current?.focus({ preventScroll: true });
  }

  async function submitQuestion(nextQuestion = question) {
    const value = nextQuestion.trim();
    if (!value || busy || !history.beginQuestion()) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: value,
    };
    followTranscript.current = true;
    setQuestion("");
    setError(null);
    setMessages((current) => [...current, userMessage]);
    setLoading(true);

    try {
      const conversationId = await history.ensureConversation(value);
      const response = await askKnowledgeBase(
        value,
        selectedKnowledgeBaseId,
        locale,
        conversationId,
      );
      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.answer,
        sources: response.sources,
        grounded: response.grounded,
      };
      setMessages((current) => [...current, assistantMessage]);
      setLatestSources(response.sources);
      setActiveCitation(null);
      history.refresh();
    } catch (caught) {
      setMessages((current) =>
        current.filter((message) => message.id !== userMessage.id),
      );
      setQuestion(value);
      setError(
        t(caught instanceof Error ? caught.message : "问答服务暂时不可用"),
      );
    } finally {
      history.endQuestion();
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submitQuestion();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      return;
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !window.matchMedia("(pointer: coarse)").matches
    ) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  const statusLabel = t(
    {
      checking: "检查服务",
      online: "检索服务在线",
      degraded: "部分服务异常",
      offline: "服务未连接",
    }[serviceState],
  );

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="workspace-shell viewport-workspace"
      ref={workspaceRef}
    >
      <button
        className="workspace-nav-trigger"
        type="button"
        aria-label={t("打开导航")}
        title={t("打开导航")}
        aria-haspopup="dialog"
        aria-expanded={navigationOpen}
        onClick={() => setNavigationOpen(true)}
      >
        <i className="bi bi-list" aria-hidden="true" />
      </button>

      <div className="workspace-layout">
        <aside className="workspace-sidebar">
          <WorkspaceNavigation
            user={currentUser}
            knowledgeBases={knowledgeBases}
            history={history}
            busy={busy}
            onDelete={() => setConfirmDelete(true)}
            onNavigate={returnToChat}
            serviceState={serviceState}
            statusLabel={statusLabel}
          />
        </aside>
        <div className="workspace-main" ref={mainScrollRef}>
          <section className="workspace-grid">
            <section
              className="conversation-panel"
              aria-label={t("知识库对话")}
            >
              <div className="conversation-heading">
                <h1>{t("知识库对话")}</h1>
                <button
                  className="workspace-show-evidence"
                  type="button"
                  onClick={showEvidence}
                >
                  {t("查看证据")} · {latestSources.length}
                </button>
              </div>

              <div
                className="transcript"
                ref={transcriptRef}
                role="log"
                aria-label={t("知识库对话")}
                tabIndex={0}
                aria-busy={busy}
                onScroll={(event) => {
                  const node = event.currentTarget;
                  followTranscript.current =
                    node.scrollHeight - node.scrollTop - node.clientHeight < 80;
                }}
              >
                {messages.map((message) => (
                  <article
                    className={`message ${message.role}`}
                    key={message.id}
                  >
                    <div className="message-label">
                      {t(
                        message.role === "assistant" ? "资料助手" : "你的问题",
                      )}
                    </div>
                    <div className="message-body">
                      {message.role === "assistant" ? (
                        <AnswerContent
                          message={
                            message.id === "welcome"
                              ? { ...message, content: t(message.content) }
                              : message
                          }
                          onCitation={(index) =>
                            openCitation(index, message.sources ?? [])
                          }
                        />
                      ) : (
                        <p>{message.content}</p>
                      )}
                      {message.role === "assistant" &&
                      message.sources?.length ? (
                        <div className="message-evidence-line">
                          <span>
                            {message.sources.length} {t("条证据")}
                          </span>
                          <span>{t("点击编号查看原文片段")}</span>
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
                        <span />
                        <span />
                        <span />
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
                    <span>
                      {t("引用来源")} ·{" "}
                      {String(latestSources.length).padStart(2, "0")}
                    </span>
                    <strong>{t("查看页码与原文")}</strong>
                  </button>
                ) : null}
                {messages.length === 1 ? (
                  <div
                    className="quick-questions"
                    role="group"
                    aria-label={t("示例问题")}
                  >
                    {QUICK_QUESTIONS.map((item) => (
                      <button
                        type="button"
                        key={item}
                        disabled={busy}
                        onClick={() => {
                          setQuestion(t(item));
                          questionRef.current?.focus();
                        }}
                      >
                        {t(item)}
                      </button>
                    ))}
                  </div>
                ) : null}
                {error ? (
                  <div className="error-banner" role="alert">
                    <strong>{t("本次请求未完成")}</strong>
                    <span>
                      {t(error)} ·{" "}
                      {t("问题已保留，可稍后重试。持续失败请联系管理员。")}
                    </span>
                  </div>
                ) : null}
                <form className="composer" onSubmit={onSubmit}>
                  <label htmlFor="question">{t("向资料库提问")}</label>
                  <div className="composer-row">
                    <textarea
                      id="question"
                      ref={questionRef}
                      aria-describedby="composer-hint"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={onKeyDown}
                      placeholder={t("例如：相关制度规定了哪些流程？")}
                      rows={1}
                      maxLength={2000}
                      disabled={busy}
                    />
                    <button
                      className="send-button"
                      type="submit"
                      disabled={!question.trim() || busy}
                    >
                      <span>{t(loading ? "检索中" : "发送问题")}</span>
                      <i className="bi bi-send" aria-hidden="true" />
                    </button>
                  </div>
                  <div className="composer-footer">
                    <p className="composer-hint" id="composer-hint">
                      <span className="desktop-input-hint">
                        {t("Enter 发送 · Shift + Enter 换行")}
                      </span>
                      <span className="touch-input-hint">
                        {t("点击按钮发送 · 回车换行")}
                      </span>
                    </p>
                    <span className="character-count">
                      {question.length} / 2000
                    </span>
                  </div>
                </form>
              </div>
            </section>

            <aside className="evidence-panel" aria-label={t("引用来源")}>
              <div className="evidence-heading">
                <div>
                  <p className="section-kicker">{t("引用证据")}</p>
                  <h2>{t("引用来源")}</h2>
                </div>
                <span className="source-count">
                  {String(latestSources.length).padStart(2, "0")}
                </span>
                <button
                  className="workspace-return-chat"
                  type="button"
                  onClick={returnToChat}
                >
                  {t("返回对话")}
                </button>
              </div>

              <div className="evidence-list" ref={evidenceListRef}>
                {latestSources.length ? (
                  <SourceCards
                    sources={latestSources}
                    idPrefix="source"
                    activeCitation={activeCitation}
                  />
                ) : (
                  <div className="evidence-empty">
                    <div className="empty-page" aria-hidden="true">
                      <span>01</span>
                    </div>
                    <h3>{t("答案的依据会显示在这里")}</h3>
                    <p>
                      {t("提问后可查看文件名、页码、相关度和命中的原文片段。")}
                    </p>
                  </div>
                )}
              </div>

              <div className="evidence-footnote">
                <span aria-hidden="true">※</span>
                <p>
                  {t(
                    "引用内容来自自动解析结果。金额、人名和设备型号等关键信息应结合原始页面复核。",
                  )}
                </p>
              </div>
            </aside>
          </section>
        </div>
      </div>
      {
        <Modal
          className="workspace-nav-drawer"
          open={navigationOpen}
          label={t("工作区导航")}
          onClose={() => setNavigationOpen(false)}
        >
          <button
            className="workspace-nav-close"
            type="button"
            aria-label={t("关闭导航")}
            title={t("关闭导航")}
            data-initial-focus
            onClick={() => setNavigationOpen(false)}
          >
            <i className="bi bi-arrow-right" aria-hidden="true" />
          </button>
          <WorkspaceNavigation
            user={currentUser}
            knowledgeBases={knowledgeBases}
            history={history}
            busy={busy}
            onDelete={() => {
              setNavigationOpen(false);
              setConfirmDelete(true);
            }}
            onNavigate={() => {
              setNavigationOpen(false);
              returnToChat();
            }}
            serviceState={serviceState}
            statusLabel={statusLabel}
          />
        </Modal>
      }

      {mobileEvidenceOpen ? (
        <Modal
          className="mobile-evidence-sheet"
          label={t("引用来源")}
          onClose={() => setMobileEvidenceOpen(false)}
        >
          <div className="mobile-sheet-handle" aria-hidden="true" />
          <header className="mobile-sheet-header">
            <div>
              <p className="section-kicker">{t("引用证据")}</p>
              <h2>
                {t("引用来源")} ·{" "}
                {String(latestSources.length).padStart(2, "0")}
              </h2>
            </div>
            <button
              type="button"
              autoFocus
              onClick={() => setMobileEvidenceOpen(false)}
              aria-label={t("关闭引用来源")}
            >
              {t("关闭")}
            </button>
          </header>
          <div className="mobile-sheet-list">
            <SourceCards
              sources={latestSources}
              idPrefix="mobile-source"
              activeCitation={activeCitation}
            />
          </div>
          <div className="evidence-footnote mobile-sheet-footnote">
            <span aria-hidden="true">※</span>
            <p>{t("金额、人名和设备型号等关键信息应结合原始页面复核。")}</p>
          </div>
        </Modal>
      ) : null}
      {confirmDelete ? (
        <Modal label={t("删除会话")} onClose={() => setConfirmDelete(false)}>
          <div className="confirmation-body">
            <p className="section-kicker">{t("会话管理")}</p>
            <h2>{t("删除会话")}</h2>
            <p>{t("删除此会话及全部消息？此操作无法撤销。")}</p>
          </div>
          <div className="modal-actions">
            <button
              type="button"
              autoFocus
              onClick={() => setConfirmDelete(false)}
            >
              {t("取消")}
            </button>
            <button
              type="button"
              className="danger-action"
              disabled={busy}
              onClick={() => {
                setConfirmDelete(false);
                void history.remove();
              }}
            >
              {t("确认删除")}
            </button>
          </div>
        </Modal>
      ) : null}
    </main>
  );
}
