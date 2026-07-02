import { type CSSProperties, useState } from "react";
import { api } from "../api";
import type { ChatPlanResponse } from "../types";

// New-query samples; follow-ups become meaningful once SQL turns are persisted (Phase 8).
const SAMPLES = [
  "Top 10 khách hàng có doanh thu cao nhất tháng gần nhất",
  "doanh thu theo công ty",
  "what did you query?",
  "cái nào cao nhất?",
  "sản phẩm họ đã mua là gì?",
  "chỉ ở Hà Nội",
];

const box: CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: 12,
  marginTop: 12,
};
const h3: CSSProperties = { fontSize: 13, margin: "0 0 8px", color: "var(--text)" };
const mono: CSSProperties = { fontFamily: "ui-monospace, monospace", fontSize: 12 };
const muted: CSSProperties = { color: "var(--muted)" };

interface Sent {
  message: string;
  resp: ChatPlanResponse;
}

export default function ChatPlanTester() {
  const [message, setMessage] = useState<string>("");
  const [conversationId, setConversationId] = useState<string | undefined>(undefined);
  const [history, setHistory] = useState<Sent[]>([]);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);

  const send = async (m?: string) => {
    const text = (m ?? message).trim();
    if (!text) return;
    if (m !== undefined) setMessage(m);
    setLoading(true);
    setErr("");
    try {
      const resp = await api.chatPlan(text, conversationId);
      setConversationId(resp.conversation_id);
      setHistory((h) => [{ message: text, resp }, ...h]);
      setMessage("");
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setConversationId(undefined);
    setHistory([]);
    setErr("");
  };

  return (
    <div className="form" style={{ maxWidth: 960 }}>
      <div style={{ fontSize: 12, ...muted }}>
        Chat-plan tester — sends a message to <code>/api/chat/plan</code> (no LLM / no SQL
        execution) and shows the classified intent, the memory-aware retrieval plan, and the
        exact compact <b>LLM skill context</b> the real turn feeds the model.
      </div>
      <div className="notice" style={{ background: "var(--panel2)", color: "var(--muted)" }}>
        The real <b>Chat</b> tab now persists every session and logs this exact model input per
        turn — open any message's “Chi tiết kỹ thuật → Ngữ cảnh gửi tới mô hình” to inspect what
        was sent. Use a saved conversation id below to plan against that session's stored memory.
      </div>

      <label>Tin nhắn (message)</label>
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="ví dụ: Top 10 khách hàng có doanh thu cao nhất tháng gần nhất"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
        }}
      />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10, alignItems: "center" }}>
        <button className="toolbtn primary" onClick={() => send()} disabled={loading || !message.trim()}>
          {loading ? "Planning…" : "Send"}
        </button>
        <button className="toolbtn" onClick={reset} disabled={loading || (!conversationId && !history.length)}>
          New conversation
        </button>
        <span style={{ ...muted, fontSize: 12 }}>
          conversation: <b style={{ color: "var(--text)" }}>{conversationId ?? "(new)"}</b>
        </span>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        {SAMPLES.map((s) => (
          <button key={s} className="chip" onClick={() => send(s)} disabled={loading}>
            {s}
          </button>
        ))}
      </div>

      {err && <div className="notice err">{err} — is the backend running on :8000 with the embedder loaded?</div>}

      {history.map((h, i) => (
        <TurnView key={history.length - i} sent={h} />
      ))}
    </div>
  );
}

function TurnView({ sent }: { sent: Sent }) {
  const { message, resp } = sent;
  const plan = resp.retrieval_plan;
  return (
    <div style={box}>
      <div style={{ ...mono, marginBottom: 8 }}>
        <span style={muted}>▸ </span>
        <b>{message}</b>
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 8 }}>
        <span style={muted}>intent <b style={{ color: "var(--accent)" }}>{plan.intent_hint}</b></span>
        <span style={muted}>needs_retrieval <b style={{ color: "var(--text)" }}>{String(plan.needs_retrieval)}</b></span>
        {plan.pinned_tables.length > 0 && (
          <span style={muted}>pinned <b style={{ color: "var(--text)" }}>{plan.pinned_tables.join(", ")}</b></span>
        )}
        {resp.resolved_context && (
          <span style={muted}>tables <b style={{ color: "var(--text)" }}>{resp.resolved_context.final_tables.length}</b></span>
        )}
      </div>
      {plan.intent_reason && (
        <div style={{ ...muted, fontSize: 12, marginBottom: 8 }}>reason: {plan.intent_reason}</div>
      )}
      {plan.retrieval_query && (
        <div style={{ ...mono, ...muted, marginBottom: 8 }}>retrieval_query: {plan.retrieval_query}</div>
      )}

      <h3 style={h3}>Memory window</h3>
      <pre className="skill">{resp.memory_window}</pre>

      <h3 style={{ ...h3, marginTop: 12 }}>LLM skill context</h3>
      <pre className="skill">{resp.llm_skill_context ?? "(none)"}</pre>
    </div>
  );
}
