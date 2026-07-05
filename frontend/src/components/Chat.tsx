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
import { t } from "../i18n";
import AnalyticReportComponent from "./AnalyticReport";
import BarChart from "./BarChart";
import ChartRenderer from "./ChartRenderer";
import ErrorBoundary from "./ErrorBoundary";
import EvidenceTable from "./EvidenceTable";
import ResultTable from "./ResultTable";
import ReviewProgress from "./ReviewProgress";

const ANALYTIC_MODES = ["ANALYTIC_MODE", "ANALYTIC_FROM_PREVIOUS_RESULT", "ANALYTIC_FOLLOWUP"];

const STARTERS = [
  "Top 10 khách hàng theo doanh thu",
  "Doanh thu theo công ty",
  "Doanh thu tháng 3 năm 2025",
  "5 sản phẩm bán chạy nhất",
  "Doanh thu của công ty An Phát",
  "Doanh thu tháng này",
];

const LS_CURRENT = "sqlnew.currentConversationId";

function arr<T = any>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

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
  review_id: string;
  report_markdown: string;
  evidence: EvidenceItem[];
  charts: ChartSpec[];
  sources: Record<string, any>[];
  caveats: string[];
  follow_up_suggestions: string[];
  analytic_status: string;
}

function fromResp(r: ChatResponse): AssistantView {
  const raw = r as any;
  return {
    answer: str(raw.answer), needs_sql: !!raw.needs_sql, error: raw.error || null, sql: raw.sql || null,
    columns: arr<string>(raw.columns), rows: arr<Record<string, any>>(raw.rows),
    row_count: Number(raw.row_count || 0), truncated: !!raw.truncated,
    intent: str(raw.intent), tables_used: arr<string>(raw.tables_used), metrics_used: arr<string>(raw.metrics_used),
    filters_used: arr<string>(raw.filters_used), validation_warnings: arr<string>(raw.validation_warnings),
    validation_errors: arr<string>(raw.validation_errors), repaired: !!raw.repaired, llm_model: str(raw.llm_model),
    timings_ms: raw.timings_ms, llm_skill_context: str(raw.llm_skill_context),
    llm_system_prompt: str(raw.llm_system_prompt), llm_user_prompt: str(raw.llm_user_prompt),
    llm_raw_response: str(raw.llm_raw_response),
    mode: str(raw.mode), review_id: str(raw.review_id), report_markdown: str(raw.report_markdown),
    evidence: arr<EvidenceItem>(raw.evidence), charts: arr<ChartSpec>(raw.charts), sources: arr<Record<string, any>>(raw.sources),
    caveats: arr<string>(raw.caveats), follow_up_suggestions: arr<string>(raw.follow_up_suggestions),
    analytic_status: str(raw.analytic_status),
  };
}

function fromHistory(t: HistoryTurn): AssistantView {
  const analytic = ANALYTIC_MODES.includes(t.intent);
  const raw = t as any;
  return {
    answer: str(raw.answer), needs_sql: !!raw.needs_sql, error: raw.error || null, sql: raw.sql || null,
    columns: arr<string>(raw.columns), rows: arr<Record<string, any>>(raw.rows),
    row_count: Number(raw.row_count || 0), truncated: !!raw.truncated,
    intent: str(raw.intent), tables_used: arr<string>(raw.tables_used), metrics_used: arr<string>(raw.metrics_used),
    filters_used: arr<string>(raw.filters_used), validation_warnings: [], validation_errors: [],
    repaired: false, llm_model: str(raw.llm_model), timings_ms: undefined,
    llm_skill_context: str(raw.llm_skill_context), llm_system_prompt: str(raw.llm_system_prompt),
    llm_user_prompt: str(raw.llm_user_prompt), llm_raw_response: str(raw.llm_raw_response),
    mode: analytic ? str(raw.intent) : "", review_id: str(raw.review_id),
    report_markdown: analytic ? str(raw.answer) : "", evidence: [], charts: [], sources: [],
    caveats: [], follow_up_suggestions: [], analytic_status: "",
  };
}

function fromReview(t: HistoryTurn, review: any): AssistantView {
  const isFollowUp = t.intent === "ANALYTIC_FOLLOWUP";
  const raw = review || {};
  return {
    ...fromHistory(t),
    answer: isFollowUp ? str(t.answer) : str(raw.findings_summary || t.answer),
    mode: isFollowUp ? str(t.intent) : str(raw.mode || t.intent),
    review_id: str(raw.review_id || t.review_id),
    report_markdown: isFollowUp ? str(t.answer) : str(raw.report_markdown || t.answer),
    evidence: arr<EvidenceItem>(raw.evidence),
    charts: arr<ChartSpec>(raw.charts),
    sources: arr<Record<string, any>>(raw.sources),
    caveats: arr<string>(raw.caveats),
    follow_up_suggestions: arr<string>(raw.follow_up_suggestions),
    analytic_status: str(raw.status),
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
  evidence: EvidenceItem[];
  charts: ChartSpec[];
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
  write: t.progress.write,
  save: "Lưu phân tích",
};

const emptyProgress = (): Progress => ({ order: [], steps: {}, streamText: "", evidence: [], charts: [] });

function applyEvent(p: Progress, ev: ChatStreamEvent): Progress {
  if (ev.type === "token") {
    return { ...p, streamText: p.streamText + str(ev.delta) };
  }
  if (ev.type === "evidence") {
    return ev.evidence ? { ...p, evidence: [...p.evidence, ev.evidence] } : p;
  }
  if (ev.type === "chart") {
    return ev.chart ? { ...p, charts: [...p.charts, ev.chart] } : p;
  }
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
  return { order, steps, streamText: p.streamText, evidence: p.evidence, charts: p.charts };
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
      if (ANALYTIC_MODES.includes(t.intent) && t.review_id) {
        try {
          const review = await api.getReview(t.review_id);
          msgs.push({ role: "assistant", view: fromReview(t, review) });
        } catch (e: any) {
          msgs.push({
            role: "assistant",
            view: {
              ...fromHistory(t),
              error: e.message || "Không tải được báo cáo đã lưu.",
            },
          });
        }
      } else {
        msgs.push({ role: "assistant", view: fromHistory(t) });
      }
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
  return (
    <>
      <ReviewProgress progress={p} labels={STEP_LABEL} onStop={onStop} />
      {p.charts.length > 0 && (
        <div className="progress-preview-block">
          {p.charts.map((chart) => (
            <ErrorBoundary key={chart.chart_id}>
              <ChartRenderer chart={chart} />
            </ErrorBoundary>
          ))}
        </div>
      )}
      {p.evidence.length > 0 && (
        <div className="progress-preview-block">
          {p.evidence.map((ev) => (
            <ErrorBoundary key={ev.evidence_id}>
              <EvidenceTable evidence={ev} />
            </ErrorBoundary>
          ))}
        </div>
      )}
    </>
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
        <ErrorBoundary>
          <AnalyticReportComponent view={view} onFollowUp={onFollowUp} />
        </ErrorBoundary>
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
