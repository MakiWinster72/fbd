export type Source = {
  index: number;
  score: number;
  document_id: string;
  document_name: string;
  page_start: number;
  page_end: number;
  section_title: string;
  excerpt: string;
  vector_score: number;
  rerank_score: number | null;
};

export type ChatResponse = {
  answer: string;
  grounded: boolean;
  sources: Source[];
};

export type HealthResponse = {
  status: "ok" | "degraded";
  dependencies: Record<string, boolean>;
};

export type CurrentUser = {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  active: boolean;
};

export type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  active: boolean;
};

export type AdminUser = CurrentUser & {
  knowledge_base_ids: string[];
};

export type ModelProfile = {
  id: string;
  name: string;
  purpose: "chat" | "embedding" | "reranker";
  protocol: "ollama" | "openai_compatible" | "tei" | "jina_compatible" | "ollama_reranker";
  base_url: string;
  model_name: string;
  vector_dim: number | null;
  options: Record<string, unknown>;
  enabled: boolean;
  has_api_key: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type ModelProfileInput = {
  name: string;
  purpose: "chat" | "embedding" | "reranker";
  protocol: "ollama" | "openai_compatible" | "tei" | "jina_compatible" | "ollama_reranker";
  base_url: string;
  api_key?: string;
  model_name: string;
  vector_dim?: number | null;
  options?: Record<string, unknown>;
};

export type ModelTestResult = {
  ok: boolean;
  sample?: string;
  dimensions?: number;
};

export type IndexJob = {
  id: string;
  profile_id: string;
  knowledge_base_id: string;
  collection_name: string;
  status: "queued" | "running" | "completed" | "failed";
  total_chunks: number;
  completed_chunks: number;
  error: string;
  created_at: string;
  updated_at: string;
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/rag-api${path}`, {
    credentials: "include",
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `服务返回 ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function askKnowledgeBase(question: string, knowledgeBaseId: string): Promise<ChatResponse> {
  return apiRequest("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify({ question, knowledge_base_id: knowledgeBaseId }),
  });
}

export function fetchHealth(): Promise<HealthResponse> {
  return apiRequest("/health");
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await apiRequest<CurrentUser>("/api/v1/auth/me");
  } catch (error) {
    if (error instanceof Error && error.message === "请先登录") return null;
    throw error;
  }
}

export function login(username: string, password: string): Promise<CurrentUser> {
  return apiRequest("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return apiRequest("/api/v1/auth/logout", { method: "POST" });
}

export function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return apiRequest("/api/v1/knowledge-bases");
}

export function listAdminUsers(): Promise<AdminUser[]> {
  return apiRequest("/api/v1/admin/users");
}

export type DocumentImportJob = {
  id: string;
  file_name: string;
  knowledge_base_id: string;
  file_hash: string;
  status: "queued" | "parsing" | "embedding" | "completed" | "failed" | "deleted";
  completed_chunks: number;
  total_chunks: number;
  error: string;
  created_at: string;
  updated_at: string;
};

export type IndexedDocument = { document_id: string; document_name: string; chunk_count: number; page_count: number };
export type IndexedDocumentDetail = IndexedDocument & { chunks: { page_start: number; page_end: number; section_title: string; content: string }[] };

export function listIndexedDocuments(knowledgeBaseId: string): Promise<IndexedDocument[]> {
  return apiRequest(`/api/v1/admin/documents?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`);
}

export function getIndexedDocument(knowledgeBaseId: string, documentId: string): Promise<IndexedDocumentDetail> {
  return apiRequest(`/api/v1/admin/documents/${encodeURIComponent(documentId)}?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`);
}

export function deleteIndexedDocument(knowledgeBaseId: string, documentId: string): Promise<void> {
  return apiRequest(`/api/v1/admin/documents/${encodeURIComponent(documentId)}?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`, { method: "DELETE" });
}

export function reindexIndexedDocument(knowledgeBaseId: string, documentId: string): Promise<DocumentImportJob> {
  return apiRequest(`/api/v1/admin/documents/${encodeURIComponent(documentId)}/reindex?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`, { method: "POST" });
}

export async function uploadDocument(file: File, knowledgeBaseId: string, ocrMode: string, duplicateMode: string): Promise<DocumentImportJob> {
  const body = new FormData();
  body.append("file", file);
  body.append("knowledge_base_id", knowledgeBaseId);
  body.append("ocr_mode", ocrMode);
  body.append("duplicate_mode", duplicateMode);
  return apiRequest("/api/v1/admin/document-imports", { method: "POST", body });
}

export function listDocumentImports(): Promise<DocumentImportJob[]> {
  return apiRequest("/api/v1/admin/document-imports");
}

export function getDocumentImport(id: string): Promise<DocumentImportJob> {
  return apiRequest(`/api/v1/admin/document-imports/${encodeURIComponent(id)}`);
}

export function retryDocumentImport(id: string): Promise<DocumentImportJob> {
  return apiRequest(`/api/v1/admin/document-imports/${encodeURIComponent(id)}/retry`, { method: "POST" });
}

export function getMineruStatus(): Promise<{ reachable: boolean }> {
  return apiRequest("/api/v1/admin/mineru-status");
}

export function listAdminKnowledgeBases(): Promise<KnowledgeBase[]> {
  return apiRequest("/api/v1/admin/knowledge-bases");
}

export function createAdminUser(input: { username: string; display_name: string; password: string; role: "admin" | "user"; knowledge_base_ids: string[] }): Promise<AdminUser> {
  return apiRequest("/api/v1/admin/users", { method: "POST", body: JSON.stringify(input) });
}

export function setAdminUserStatus(id: string, active: boolean): Promise<AdminUser> {
  return apiRequest(`/api/v1/admin/users/${id}/status`, { method: "PATCH", body: JSON.stringify({ active }) });
}

export function resetAdminUserPassword(id: string, password: string): Promise<void> {
  return apiRequest(`/api/v1/admin/users/${id}/reset-password`, { method: "POST", body: JSON.stringify({ password }) });
}

export function setAdminUserKnowledgeBases(id: string, knowledgeBaseIds: string[]): Promise<AdminUser> {
  return apiRequest(`/api/v1/admin/users/${id}/knowledge-bases`, { method: "PUT", body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }) });
}

export function listModelProfiles(): Promise<ModelProfile[]> {
  return apiRequest("/api/v1/admin/model-profiles");
}

export function createModelProfile(input: ModelProfileInput): Promise<ModelProfile> {
  return apiRequest("/api/v1/admin/model-profiles", { method: "POST", body: JSON.stringify(input) });
}

export function updateModelProfile(id: string, input: Partial<ModelProfileInput> & { clear_api_key?: boolean; enabled?: boolean }): Promise<ModelProfile> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function deleteModelProfile(id: string): Promise<void> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}`, { method: "DELETE" });
}

export function testModelProfile(id: string): Promise<ModelTestResult> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}/test`, { method: "POST" });
}

export function activateChatProfile(id: string): Promise<ModelProfile> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}/activate-chat`, { method: "POST" });
}

export function activateRerankerProfile(id: string): Promise<ModelProfile> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}/activate-reranker`, { method: "POST" });
}

export function deactivateRerankerProfile(): Promise<void> {
  return apiRequest("/api/v1/admin/model-profiles/deactivate-reranker", { method: "POST" });
}

export function activateEmbeddingProfile(id: string): Promise<IndexJob> {
  return apiRequest(`/api/v1/admin/model-profiles/${id}/activate-embedding`, {
    method: "POST",
    body: JSON.stringify({ knowledge_base_id: "default" }),
  });
}

export function getIndexJob(id: string): Promise<IndexJob> {
  return apiRequest(`/api/v1/admin/index-jobs/${id}`);
}
