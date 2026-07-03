import type {
  ChatPlanResponse,
  ChatResponse,
  ChatStreamEvent,
  ConversationDetail,
  ConversationSummary,
  Entry,
  HistoryRow,
  ResolvedContext,
  SaveResult,
  Status,
} from "./types";

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
  // Phase 10: audit history + restore + live-update helpers.
  entryHistory: (id: string) =>
    req<{ entry_id: string; history: HistoryRow[] }>(`/entries/${encodeURIComponent(id)}/history`),
  restoreEntry: (id: string, history_id: number) =>
    req<SaveResult>(`/entries/${encodeURIComponent(id)}/restore`, {
      method: "POST",
      body: JSON.stringify({ history_id }),
    }),
  kbVersion: () => req<{ kb_version: number }>("/kb/version"),
  embedPending: () =>
    req<{ embedded: number; errors: number; index_size: number }>("/embed-pending", { method: "POST" }),
  syncValues: () =>
    req<{ staged: number; kb_version: number; embed: any }>("/knowledge/sync-values", { method: "POST" }),
  health: () => req<any>("/health"),
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

  // Persistent chat sessions.
  listConversations: () => req<ConversationSummary[]>("/conversations"),
  getConversation: (id: string) => req<ConversationDetail>(`/conversations/${encodeURIComponent(id)}`),
  renameConversation: (id: string, title: string) =>
    req<ConversationSummary>(`/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteConversation: (id: string) =>
    req<{ deleted: boolean; id: string }>(`/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  // Streaming turn: emits step + token events, resolves to the final ChatResponse.
  chatStream: async (
    message: string,
    conversation_id: string | undefined,
    onEvent: (ev: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<ChatResponse> => {
    const res = await fetch(BASE + "/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`${res.status}: ${res.statusText}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let final: ChatResponse | null = null;

    const handleFrame = (frame: string) => {
      // An SSE frame is one or more lines; we only emit `data:` payloads.
      const dataLines = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim());
      if (!dataLines.length) return;
      const payload = dataLines.join("\n");
      let ev: ChatStreamEvent;
      try {
        ev = JSON.parse(payload) as ChatStreamEvent;
      } catch {
        return;
      }
      if (ev.type === "final") final = ev.response;
      onEvent(ev);
    };

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        if (frame.trim()) handleFrame(frame);
      }
    }
    if (buf.trim()) handleFrame(buf);
    if (!final) throw new Error("stream ended without a final response");
    return final;
  },
};
