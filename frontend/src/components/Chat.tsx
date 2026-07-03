import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
  ChatResponse,
  ChatStreamEvent,
  ConversationSummary,
  EvidenceItem,
  ChartSpec,
  HistoryTurn,
} from "../types";
import BarChart from "./BarChart";
import ResultTable from "./ResultTable";

const ANALYTIC_MODES = ["ANALYTIC_MODE", "ANALYTIC_FROM_PREVIOUS_RESULT"];

const STARTERS = [
  "Top 10 khách hàng theo doanh thu",
  "Doanh thu theo công ty",
  "Doanh thu tháng 3 năm 2025",
  "5 sản phẩm bán chạy nhất",
  "Doanh thu của công ty An Phát",
  "Doanh thu tháng này",
];

const LS_CURRENT = "sqlnew.currentConversationId";

// ---- a common view model so live responses and reopened history render the same ----
interface AssistantView {
  answer: string;
  needs_sql: boolean;
  error: string | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  truncated: boolean;
  intent: string;
  tables_used: string[];
  metrics_used: string[];
  filters_used: string[];
  validation_warnings: string[];
  validation_errors: string[];
  repaired: boolean;
  llm_model: string;
  timings_ms?: Record<string, number>;
  llm_skill_context: string;
  llm_system_prompt: string;
  llm_user_prompt: string;
  llm_raw_response: string;
  // ---- analytic turn (empty on a normal turn) ----
  mode: string;
  report_markdown: string;
  evidence: EvidenceItem[];
  charts: ChartSpec[];
  caveats: string[];
  follow_up_suggestions: string[];
  analytic_status: string;
}

function fromResp(r: ChatResponse): AssistantView {
  return {
    answer: r.answer, needs_sql: r.needs_sql, error: r.error, sql: r.sql,
    columns: r.columns, rows: r.rows, row_count: r.row_count, truncated: r.truncated,
    intent: r.intent, tables_used: r.tables_used, metrics_used: r.metrics_used,
    filters_used: r.filters_used, validation_warnings: r.validation_warnings,
    validation_errors: r.validation_errors, repaired: r.repaired, llm_model: r.llm_model,
    timings_ms: r.timings_ms, llm_skill_context: r.llm_skill_context,
    llm_system_prompt: r.llm_system_prompt, llm_user_prompt: r.llm_user_prompt,
    llm_raw_response: r.llm_raw_response,
    mode: r.mode || "", report_markdown: r.report_markdown || "",
    evidence: r.evidence || [], charts: r.charts || [], caveats: r.caveats || [],
    follow_up_suggestions: r.follow_up_suggestions || [], analytic_status: r.analytic_status || "",
  };
}

function fromHistory(t: HistoryTurn): AssistantView {
  const analytic = ANALYTIC_MODES.includes(t.intent);
  return {
    answer: t.answer, needs_sql: t.needs_sql, error: t.error || null, sql: t.sql || null,
    columns: t.columns, rows: t.rows, row_count: t.row_count, truncated: t.truncated,
    intent: t.intent, tables_used: t.tables_used, metrics_used: t.metrics_used,
    filters_used: t.filters_used, validation_warnings: [], validation_errors: [],
    repaired: false, llm_model: t.llm_model, timings_ms: undefined,
    llm_skill_context: t.llm_skill_context, llm_system_prompt: t.llm_system_prompt,
    llm_user_prompt: t.llm_user_prompt, llm_raw_response: t.llm_raw_response,
    // Full analytic re-render (evidence/charts from the stored review) lands in Phase 16;
    // for now the persisted summary text carries the answer.
    mode: analytic ? t.intent : "", report_markdown: "", evidence: [], charts: [],
    caveats: [], follow_up_suggestions: [], analytic_status: "",
  };
}

type ChatMessage =
  | { role: "user"; text: string }
  | { role: "assistant"; view: AssistantView };

// ---- streaming progress ----------------------------------------------------
type StepStatus = "active" | "done" | "error" | "skipped";
interface StepState {
  status: StepStatus;
  note?: string;
}
interface Progress {
  order: string[];
  steps: Record<string, StepState>;
  streamText: string;
}

const STEP_LABEL: Record<string, string> = {
  plan: "Phân tích câu hỏi & ý định",
  retrieve: "Tìm ngữ cảnh dữ liệu",
  llm: "Mô hình soạn câu trả lời",
  repair: "Tự sửa lại câu SQL",
  validate: "Kiểm tra an toàn câu SQL",
  execute: "Chạy truy vấn trên dữ liệu",
  summarize: "Tổng hợp kết quả",
  // analytic steps (Phase 13/14)
  mode: "Chọn chế độ trả lời",
  task: "Chạy truy vấn phân tích",
  profile: "Tổng hợp bằng chứng",
  charts: "Dựng biểu đồ",
  save: "Lưu phân tích",
};

const emptyProgress = (): Progress => ({ order: [], steps: {}, streamText: "" });

function applyEvent(p: Progress, ev: ChatStreamEvent): Progress {
  if (ev.type === "token") {
    return { ...p, streamText: p.streamText + ev.delta };
  }
  // evidence/chart events feed the final response; the stepper ignores them live.
  if (ev.type !== "step") return p;
  const order = p.order.includes(ev.step) ? p.order : [...p.order, ev.step];
  const steps = { ...p.steps };
  if (ev.status === "start") {
    // A repeated step key (e.g. "task" per query) stays active but refreshes its note.
    steps[ev.step] = {
      status: "active",
      note: ev.step === "task" && ev.task_total
        ? `${ev.task_index}/${ev.task_total}${ev.title ? " · " + ev.title : ""}`
        : steps[ev.step]?.note,
    };
  } else {
    let status: StepStatus = "done";
    let note: string | undefined;
    if (ev.step === "plan") {
      note = ev.intent ? `ý định: ${ev.intent}` :
        ev.task_count != null ? `${ev.task_count} truy vấn${ev.source === "fallback" ? " (mẫu)" : ""}` : undefined;
    } else if (ev.step === "mode") {
      note = ev.mode;
    } else if (ev.step === "retrieve") {
      if (ev.skipped) { status = "skipped"; note = "dùng ngữ cảnh trước đó"; }
      else note = ev.tables?.length ? `${ev.tables.length} bảng: ${ev.tables.join(", ")}` : undefined;
    } else if (ev.step === "llm") {
      if (ev.error) { status = "error"; note = ev.error; }
      else note = ev.ms ? `${(ev.ms / 1000).toFixed(1)}s` : undefined;
    } else if (ev.step === "validate") {
      status = ev.ok === false ? "error" : "done";
      if (ev.ok === false) note = (ev.errors || []).join(" • ") || "SQL không hợp lệ";
      else if (ev.repaired) note = "đã sửa & hợp lệ";
    } else if (ev.step === "repair") {
      status = ev.ok ? "done" : "error";
    } else if (ev.step === "execute") {
      if (ev.ok === false) { status = "error"; note = ev.error; }
      else note = `${ev.row_count ?? 0} dòng`;
    } else if (ev.step === "task") {
      status = ev.task_status === "failed" ? "error" : ev.task_status === "skipped" ? "skipped" : "done";
      note = `${ev.task_index}/${ev.task_total}${ev.title ? " · " + ev.title : ""}`;
    } else if (ev.step === "profile") {
      note = ev.evidence_count != null ? `${ev.evidence_count} bằng chứng` : undefined;
    } else if (ev.step === "charts") {
      note = ev.chart_count != null ? `${ev.chart_count} biểu đồ` : undefined;
    } else if (ev.step === "save") {
      note = ev.review_status;
    }
    steps[ev.step] = { status, note };
  }
  return { order, steps, streamText: p.streamText };
}

// Pull the friendly `answer` value out of the partial streamed JSON so the user sees a
// readable preview instead of raw JSON while the model writes.
function extractAnswerPreview(raw: string): string {
  const key = raw.indexOf('"answer"');
  if (key === -1) return "";
  const colon = raw.indexOf(":", key + 8);
  if (colon === -1) return "";
  let i = colon + 1;
  while (i < raw.length && raw[i] !== '"') i++;
  if (i >= raw.length) return "";
  i++; // past opening quote
  let out = "";
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === "\\") {
      const nx = raw[i + 1];
      if (nx === "n") out += "\n";
      else if (nx === "t") out += "\t";
      else if (nx !== undefined) out += nx;
      i += 2;
      continue;
    }
    if (ch === '"') break; // closing quote
    out += ch;
    i++;
  }
  return out;
}

export default function Chat() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [err, setErr] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations());
    } catch {
      /* backend may be down; the send() path surfaces that */
    }
  }, []);

  // Load the session list once, then restore the last-open conversation if any.
  useEffect(() => {
    (async () => {
      await refreshConversations();
      const last = localStorage.getItem(LS_CURRENT);
      if (last) {
        try {
          await openConversation(last, false);
        } catch {
          localStorage.removeItem(LS_CURRENT);
        }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, progress]);

  const openConversation = async (id: string, refresh = true) => {
    const detail = await api.getConversation(id);
    const msgs: ChatMessage[] = [];
    for (const t of detail.turns) {
      msgs.push({ role: "user", text: t.user_question });
      msgs.push({ role: "assistant", view: fromHistory(t) });
    }
    setMessages(msgs);
    setConversationId(id);
    localStorage.setItem(LS_CURRENT, id);
    setErr("");
    if (refresh) refreshConversations();
  };

  const newConversation = () => {
    if (loading) return;
    setConversationId(undefined);
    setMessages([]);
    setErr("");
    setInput("");
    localStorage.removeItem(LS_CURRENT);
  };

  const removeConversation = async (id: string) => {
    if (!window.confirm("Xoá cuộc trò chuyện này?")) return;
    try {
      await api.deleteConversation(id);
      if (id === conversationId) newConversation();
      refreshConversations();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  };

  const commitRename = async (id: string) => {
    const title = editingTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await api.renameConversation(id, title);
      setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  };

  const send = async (m?: string) => {
    const text = (m ?? input).trim();
    if (!text || loading) return;
    setErr("");
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);
    setProgress(emptyProgress());
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const resp = await api.chatStream(
        text,
        conversationId,
        (ev) => setProgress((p) => applyEvent(p ?? emptyProgress(), ev)),
        ac.signal,
      );
      setConversationId(resp.conversation_id);
      localStorage.setItem(LS_CURRENT, resp.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", view: fromResp(resp) }]);
      refreshConversations();
    } catch (e: any) {
      if (e?.name === "AbortError") {
        setErr("Đã dừng tạo câu trả lời.");
      } else {
        // Fall back to the non-streaming endpoint if streaming failed outright.
        try {
          const resp = await api.chat(text, conversationId);
          setConversationId(resp.conversation_id);
          localStorage.setItem(LS_CURRENT, resp.conversation_id);
          setMessages((prev) => [...prev, { role: "assistant", view: fromResp(resp) }]);
          refreshConversations();
        } catch (e2: any) {
          setErr(e2.message || String(e2));
        }
      }
    } finally {
      setLoading(false);
      setProgress(null);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  return (
    <div className="chat-shell">
      <aside className="conv-sidebar">
        <button className="conv-new" onClick={newConversation} disabled={loading}>
          ＋ Cuộc trò chuyện mới
        </button>
        <div className="conv-list">
          {conversations.length === 0 && <div className="conv-empty">Chưa có phiên nào.</div>}
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`conv-item${c.id === conversationId ? " active" : ""}`}
              onClick={() => editingId !== c.id && openConversation(c.id)}
            >
              {editingId === c.id ? (
                <input
                  className="conv-rename"
                  value={editingTitle}
                  autoFocus
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={() => commitRename(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(c.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <>
                  <div className="conv-item-main">
                    <div className="conv-title">{c.title || "Cuộc trò chuyện"}</div>
                    <div className="conv-sub">{c.turn_count} lượt</div>
                  </div>
                  <div className="conv-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      className="conv-iconbtn"
                      title="Đổi tên"
                      onClick={() => { setEditingId(c.id); setEditingTitle(c.title); }}
                    >
                      ✎
                    </button>
                    <button
                      className="conv-iconbtn danger"
                      title="Xoá"
                      onClick={() => removeConversation(c.id)}
                    >
                      🗑
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      </aside>

      <div className="chat">
        <div className="chat-header">
          <div className="chat-title">
            <span className="chat-logo">📊</span>
            <div>
              <div className="chat-title-main">Trợ lý dữ liệu bán hàng</div>
              <div className="chat-title-sub">Hỏi bằng tiếng Việt về doanh thu, khách hàng, sản phẩm…</div>
            </div>
          </div>
          <button className="toolbtn" onClick={newConversation} disabled={loading || messages.length === 0}>
            ＋ Mới
          </button>
        </div>

        <div className="chat-log" ref={logRef}>
          {messages.length === 0 && !loading && (
            <div className="chat-welcome">
              <div className="chat-welcome-emoji">👋</div>
              <div className="chat-welcome-title">Xin chào! Bạn muốn xem dữ liệu gì?</div>
              <div className="chat-welcome-sub">Chọn một gợi ý bên dưới hoặc tự nhập câu hỏi.</div>
              <div className="chat-starters">
                {STARTERS.map((s) => (
                  <button key={s} className="chat-starter" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="chat-row user">
                <div className="chat-bubble user">{m.text}</div>
              </div>
            ) : (
              <div key={i} className="chat-row bot">
                <div className="chat-avatar">🤖</div>
                <AssistantBubble view={m.view} onFollowUp={send} />
              </div>
            ),
          )}

          {loading && (
            <div className="chat-row bot">
              <div className="chat-avatar">🤖</div>
              <div className="chat-bubble bot progress">
                <ProgressPanel progress={progress} onStop={stop} />
              </div>
            </div>
          )}
        </div>

        {err && <div className="notice err chat-err">{err} — máy chủ có đang chạy ở cổng 8000 không?</div>}

        <div className="chat-inputbar">
          <textarea
            className="chat-input"
            placeholder="Nhập câu hỏi… (Enter để gửi, Shift+Enter để xuống dòng)"
            value={input}
            rows={1}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="chat-send" onClick={() => send()} disabled={loading || !input.trim()}>
            ➤
          </button>
        </div>
      </div>
    </div>
  );
}

function ProgressPanel({ progress, onStop }: { progress: Progress | null; onStop: () => void }) {
  const p = progress ?? emptyProgress();
  const preview = extractAnswerPreview(p.streamText);
  const llmActive = p.steps["llm"]?.status === "active";
  return (
    <div className="stepper">
      <div className="stepper-head">
        <span className="stepper-title">Đang xử lý…</span>
        <button className="chat-linkbtn" onClick={onStop}>
          ✕ Dừng
        </button>
      </div>
      {p.order.length === 0 && (
        <div className="chat-typing">
          <span></span>
          <span></span>
          <span></span>
        </div>
      )}
      {p.order.map((key) => {
        const st = p.steps[key];
        return (
          <div key={key} className={`step ${st.status}`}>
            <span className="step-icon">
              {st.status === "active" ? (
                <span className="spinner" />
              ) : st.status === "done" ? (
                "✓"
              ) : st.status === "error" ? (
                "✕"
              ) : (
                "–"
              )}
            </span>
            <span className="step-label">{STEP_LABEL[key] ?? key}</span>
            {st.note && <span className="step-note">{st.note}</span>}
          </div>
        );
      })}
      {llmActive && (preview || p.streamText) && (
        <div className="stream-preview">
          {preview ? preview : <span className="stream-raw">{p.streamText.slice(-280)}</span>}
          <span className="stream-caret" />
        </div>
      )}
    </div>
  );
}

function AssistantBubble({
  view,
  onFollowUp,
}: {
  view: AssistantView;
  onFollowUp: (m: string) => void;
}) {
  const isAnalytic = ANALYTIC_MODES.includes(view.mode);
  const hasRows = view.needs_sql && view.row_count > 0 && view.columns.length > 0;
  const emptyData = view.needs_sql && !view.error && view.row_count === 0;

  if (isAnalytic && (view.evidence.length > 0 || view.report_markdown)) {
    return (
      <div className={`chat-bubble bot analytic${view.error ? " has-error" : ""}`}>
        <AnalyticReport view={view} onFollowUp={onFollowUp} />
        <TechDetails view={view} />
      </div>
    );
  }

  return (
    <div className={`chat-bubble bot${view.error ? " has-error" : ""}`}>
      <div className="chat-answer">{view.answer}</div>

      {emptyData && <div className="chat-empty">📭 Không có dòng dữ liệu nào cho yêu cầu này.</div>}

      {hasRows && (
        <>
          <BarChart columns={view.columns} rows={view.rows} />
          <ResultTable columns={view.columns} rows={view.rows} />
        </>
      )}

      <TechDetails view={view} />
    </div>
  );
}

// ---- analytic report (Phase 13/14 interim: tables-first; recharts UI is Phase 16) ----
function renderMarkdownLite(md: string): JSX.Element[] {
  return md.split("\n").map((line, i) => {
    if (line.startsWith("## ")) return <h4 key={i} className="analytic-h">{line.slice(3)}</h4>;
    if (line.startsWith("> ")) return <blockquote key={i} className="analytic-note">{line.slice(2)}</blockquote>;
    if (line.startsWith("- ")) return <li key={i} className="analytic-li">{line.slice(2)}</li>;
    if (!line.trim()) return <div key={i} className="analytic-gap" />;
    return <p key={i} className="analytic-p">{line}</p>;
  });
}

function AnalyticReport({
  view,
  onFollowUp,
}: {
  view: AssistantView;
  onFollowUp: (m: string) => void;
}) {
  const badge =
    view.analytic_status === "degraded" ? "⚠ một phần" :
    view.analytic_status === "failed" ? "✕ thiếu dữ liệu" : "✓ hoàn tất";
  return (
    <div className="analytic-report">
      <div className="analytic-badge">Phân tích chuyên sâu · {badge}</div>
      {view.report_markdown && <div className="analytic-md">{renderMarkdownLite(view.report_markdown)}</div>}

      {view.evidence.length > 0 && (
        <div className="analytic-evidence">
          {view.evidence.map((ev) => (
            <div key={ev.evidence_id} className="evidence-block">
              <div className="evidence-title">
                {ev.title}
                {ev.status !== "success" && <span className="evidence-status"> · {ev.status}</span>}
              </div>
              {ev.rows.length > 0 && ev.columns.length > 0 ? (
                <ResultTable columns={ev.columns} rows={ev.rows} />
              ) : (
                <div className="chat-empty">📭 Không có dữ liệu cho bước này.</div>
              )}
            </div>
          ))}
        </div>
      )}

      {view.follow_up_suggestions.length > 0 && (
        <div className="analytic-followups">
          {view.follow_up_suggestions.map((s) => (
            <button key={s} className="chat-starter followup-chip" onClick={() => onFollowUp(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TechDetails({ view }: { view: AssistantView }) {
  const copy = (text?: string | null) => text && navigator.clipboard?.writeText(text);
  const chips: [string, string][] = [
    ["intent", view.intent],
    ...(view.tables_used.length ? ([["bảng", view.tables_used.join(", ")]] as [string, string][]) : []),
    ...(view.metrics_used.length ? ([["chỉ số", view.metrics_used.join(", ")]] as [string, string][]) : []),
    ...(view.filters_used.length ? ([["bộ lọc", view.filters_used.join(", ")]] as [string, string][]) : []),
    ...(view.repaired ? ([["đã sửa SQL", "có"]] as [string, string][]) : []),
    ...(view.llm_model ? ([["mô hình", view.llm_model]] as [string, string][]) : []),
  ];
  const total = view.timings_ms?.total;
  const hasModelInput = !!(view.llm_skill_context || view.llm_user_prompt || view.llm_raw_response);
  return (
    <details className="chat-tech">
      <summary>Chi tiết kỹ thuật</summary>
      <div className="chat-tech-body">
        {view.sql && (
          <div className="chat-sql-block">
            <div className="chat-sql-head">
              <span>SQL</span>
              <button className="chat-linkbtn" onClick={() => copy(view.sql)}>
                ⧉ Sao chép
              </button>
            </div>
            <pre className="skill chat-sql">{view.sql}</pre>
          </div>
        )}
        <div className="chat-meta">
          {chips.map(([k, v]) => (
            <span key={k} className="chat-metachip">
              <b>{k}:</b> {v}
            </span>
          ))}
          {typeof total === "number" && (
            <span className="chat-metachip">
              <b>thời gian:</b> {total} ms
            </span>
          )}
        </div>
        {view.validation_warnings.length > 0 && (
          <div className="chat-warn">⚠ {view.validation_warnings.join(" • ")}</div>
        )}
        {view.validation_errors.length > 0 && (
          <div className="chat-warn err">✕ {view.validation_errors.join(" • ")}</div>
        )}
        {view.error && <div className="chat-warn err">Lỗi: {view.error}</div>}

        {hasModelInput && (
          <details className="chat-modelio">
            <summary>Ngữ cảnh gửi tới mô hình (model input)</summary>
            <div className="chat-modelio-body">
              {view.llm_skill_context && (
                <ModelBlock label="Skill context (§27)" text={view.llm_skill_context} onCopy={copy} />
              )}
              {view.llm_system_prompt && (
                <ModelBlock label="System prompt" text={view.llm_system_prompt} onCopy={copy} collapsed />
              )}
              {view.llm_user_prompt && (
                <ModelBlock label="User prompt" text={view.llm_user_prompt} onCopy={copy} collapsed />
              )}
              {view.llm_raw_response && (
                <ModelBlock label="Model output (raw JSON)" text={view.llm_raw_response} onCopy={copy} collapsed />
              )}
            </div>
          </details>
        )}
      </div>
    </details>
  );
}

function ModelBlock({
  label,
  text,
  onCopy,
  collapsed,
}: {
  label: string;
  text: string;
  onCopy: (t: string) => void;
  collapsed?: boolean;
}) {
  const body = (
    <div className="chat-sql-block">
      <div className="chat-sql-head">
        <span>{label}</span>
        <button className="chat-linkbtn" onClick={() => onCopy(text)}>
          ⧉ Sao chép
        </button>
      </div>
      <pre className="skill chat-modelio-pre">{text}</pre>
    </div>
  );
  if (!collapsed) return body;
  return (
    <details className="chat-modelio-sub">
      <summary>{label}</summary>
      <pre className="skill chat-modelio-pre">{text}</pre>
      <button className="chat-linkbtn" onClick={() => onCopy(text)}>
        ⧉ Sao chép
      </button>
    </details>
  );
}
