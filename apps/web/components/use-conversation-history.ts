"use client";

import { useEffect, useRef, useState } from "react";
import { createConversation, deleteConversation, getConversation, listConversations, type Conversation, type ConversationDetail, type KnowledgeBase } from "@/lib/api";

function writeConversationUrl(id: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (id) params.set("conversation", id);
  else params.delete("conversation");
  const search = params.toString();
  try {
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
  } catch {
    // Browser restrictions must not fail a saved conversation; the history list remains available.
  }
}

export function useConversationHistory(userId: string, knowledgeBases: KnowledgeBase[], answering: boolean) {
  const enabled = userId !== "development";
  const [knowledgeBaseId, setKnowledgeBaseId] = useState(knowledgeBases[0].id);
  const [active, setActive] = useState<Conversation | null>(null);
  const [items, setItems] = useState<Conversation[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [restoring, setRestoring] = useState(enabled);
  const [listing, setListing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const nextOffset = useRef(0);
  const moreBusy = useRef(false);
  const [revision, setRevision] = useState(0);
  const [replay, setReplay] = useState<{ detail: ConversationDetail | null }>({ detail: null });
  const viewVersion = useRef(0);
  const listVersion = useRef(0);
  const alive = useRef(true);
  const viewBusy = useRef(enabled);
  const questionBusy = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; viewVersion.current += 1; listVersion.current += 1; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const version = ++viewVersion.current;
    const id = enabled ? new URLSearchParams(window.location.search).get("conversation") : null;
    setActive(null);
    setKnowledgeBaseId(knowledgeBases[0].id);
    setReplay({ detail: null });
    viewBusy.current = Boolean(id);
    setRestoring(Boolean(id));
    if (id) {
      getConversation(id).then((detail) => {
        if (cancelled || version !== viewVersion.current) return;
        if (!knowledgeBases.some((item) => item.id === detail.conversation.knowledge_base_id)) throw new Error("没有访问该知识库的权限");
        setActive(detail.conversation);
        setKnowledgeBaseId(detail.conversation.knowledge_base_id);
        setReplay({ detail });
        setError(null);
      }).catch((reason) => {
        if (cancelled || version !== viewVersion.current) return;
        setError(reason instanceof Error ? reason.message : "无法恢复会话");
        writeConversationUrl(null);
      }).finally(() => { if (!cancelled && version === viewVersion.current) { viewBusy.current = false; setRestoring(false); } });
    }
    return () => { cancelled = true; };
  }, [userId, enabled, knowledgeBases]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const version = ++listVersion.current;
    setListing(true);
    setListError(null);
    nextOffset.current = 0;
    moreBusy.current = false;
    setItems([]);
    listConversations(knowledgeBaseId).then((result) => {
      if (cancelled || version !== listVersion.current) return;
      nextOffset.current = result.items.length;
      setItems(result.items);
      setHasMore(result.has_more);
    }).catch((reason) => {
      if (!cancelled && version === listVersion.current) setListError(reason instanceof Error ? reason.message : "无法加载会话历史");
    }).finally(() => { if (!cancelled && version === listVersion.current) setListing(false); });
    return () => { cancelled = true; };
  }, [enabled, userId, knowledgeBaseId, revision]);

  function refresh() { setRevision((value) => value + 1); }

  function beginQuestion() {
    if (viewBusy.current || questionBusy.current) return false;
    questionBusy.current = true;
    return true;
  }

  function endQuestion() { questionBusy.current = false; }

  function startNew() {
    if (answering || restoring || questionBusy.current || viewBusy.current) return;
    viewVersion.current += 1;
    setActive(null);
    setReplay({ detail: null });
    setError(null);
    writeConversationUrl(null);
  }

  function changeKnowledgeBase(id: string) {
    if (answering || restoring || questionBusy.current || viewBusy.current || !knowledgeBases.some((item) => item.id === id)) return;
    listVersion.current += 1;
    startNew();
    setKnowledgeBaseId(id);
  }

  async function open(id: string): Promise<boolean> {
    if (!id || answering || restoring || questionBusy.current || viewBusy.current) return false;
    const version = ++viewVersion.current;
    viewBusy.current = true;
    setRestoring(true);
    setError(null);
    try {
      const detail = await getConversation(id);
      if (!alive.current || version !== viewVersion.current) return false;
      if (!knowledgeBases.some((item) => item.id === detail.conversation.knowledge_base_id)) throw new Error("没有访问该知识库的权限");
      setActive(detail.conversation);
      setKnowledgeBaseId(detail.conversation.knowledge_base_id);
      setReplay({ detail });
      writeConversationUrl(id);
      return true;
    } catch (reason) {
      if (alive.current && version === viewVersion.current) setError(reason instanceof Error ? reason.message : "无法恢复会话");
      return false;
    } finally {
      if (alive.current && version === viewVersion.current) { viewBusy.current = false; setRestoring(false); }
    }
  }

  async function ensureConversation(title: string): Promise<string | undefined> {
    if (!enabled) return undefined;
    if (active) return active.id;
    const version = viewVersion.current;
    const created = await createConversation(knowledgeBaseId, Array.from(title).slice(0, 100).join(""));
    if (!alive.current || version !== viewVersion.current) throw new Error("会话已切换，请重新提问");
    setActive(created);
    writeConversationUrl(created.id);
    refresh();
    return created.id;
  }

  async function remove() {
    if (!active || answering || restoring || questionBusy.current || viewBusy.current) return;
    const id = active.id;
    const version = ++viewVersion.current;
    viewBusy.current = true;
    setRestoring(true);
    setError(null);
    try {
      await deleteConversation(id);
      if (!alive.current || version !== viewVersion.current) return;
      setActive(null);
      setReplay({ detail: null });
      writeConversationUrl(null);
      refresh();
    } catch (reason) {
      if (alive.current && version === viewVersion.current) setError(reason instanceof Error ? reason.message : "删除会话失败");
    } finally {
      if (alive.current && version === viewVersion.current) { viewBusy.current = false; setRestoring(false); }
    }
  }

  async function loadMore() {
    if (!hasMore || listing || restoring || answering || moreBusy.current) return;
    moreBusy.current = true;
    const version = listVersion.current;
    setListing(true);
    try {
      const result = await listConversations(knowledgeBaseId, nextOffset.current);
      if (!alive.current || version !== listVersion.current) return;
      nextOffset.current += result.items.length;
      setListError(null);
      setItems((current) => [...current, ...result.items.filter((item) => !current.some((existing) => existing.id === item.id))]);
      setHasMore(result.has_more);
    } catch (reason) {
      if (alive.current && version === listVersion.current) setListError(reason instanceof Error ? reason.message : "无法加载会话历史");
    } finally {
      if (alive.current && version === listVersion.current) { moreBusy.current = false; setListing(false); }
    }
  }

  return { enabled, knowledgeBaseId, active, items, hasMore, restoring, listing, error: error ?? listError, replay, open, remove, startNew, changeKnowledgeBase, ensureConversation, refresh, loadMore, beginQuestion, endQuestion };
}
