import type { ChatPlanResponse, ChatResponse, Entry, ResolvedContext, SaveResult, Status } from "./types";

const BASE = "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export interface Meta {
  entry_types: string[];
  tables: { name: string; primary_key: string; columns: { name: string; type: string }[] }[];
  foreign_keys: { from_table: string; from_column: string; to_table: string; to_column: string }[];
}

export const api = {
  status: () => req<Status>("/status"),
  meta: () => req<Meta>("/meta"),
  listEntries: (params: { type?: string; q?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.type) qs.set("type", params.type);
    if (params.q) qs.set("q", params.q);
    if (params.status) qs.set("status", params.status);
    const s = qs.toString();
    return req<Entry[]>("/entries" + (s ? `?${s}` : ""));
  },
  getEntry: (id: string) => req<Entry>(`/entries/${encodeURIComponent(id)}`),
  createEntry: (payload: { type: string; body: any; name?: string; enabled?: boolean }) =>
    req<SaveResult>("/entries", { method: "POST", body: JSON.stringify(payload) }),
  updateEntry: (id: string, payload: { type: string; body: any; name?: string; enabled?: boolean }) =>
    req<SaveResult>(`/entries/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  reembed: (id: string) =>
    req<SaveResult>(`/entries/${encodeURIComponent(id)}/reembed`, { method: "POST" }),
  deleteEntry: (id: string) =>
    req<{ deleted: boolean; id: string }>(`/entries/${encodeURIComponent(id)}`, { method: "DELETE" }),
  skillMd: () => req<{ markdown: string }>("/skill-md"),
  rebuildSkillMd: () => req<{ path: string }>("/rebuild/skill-md", { method: "POST" }),
  exportDocs: () => req<{ doc_count: number; by_type: Record<string, number> }>("/export-docs", { method: "POST" }),
  rebuildEmbeddings: () => req<{ embedded: number; errors: number; index_size: number }>("/rebuild/embeddings", { method: "POST" }),
  seed: (reset: boolean) => req<any>(`/seed?reset=${reset}&embed=true`, { method: "POST" }),
  // Phase 3/4: query-time retrieval + memory-aware planning preview.
  retrieve: (query: string, pinned_tables: string[] = []) =>
    req<ResolvedContext>("/retrieve", {
      method: "POST",
      body: JSON.stringify({ query, pinned_tables }),
    }),
  chatPlan: (message: string, conversation_id?: string) =>
    req<ChatPlanResponse>("/chat/plan", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id }),
    }),
  // Phase 7/8: the real conversational turn (LLM + SQL validate/execute + memory).
  chat: (message: string, conversation_id?: string) =>
    req<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id }),
    }),
};
