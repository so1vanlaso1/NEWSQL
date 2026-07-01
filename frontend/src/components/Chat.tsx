import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatResponse } from "../types";
import BarChart from "./BarChart";
import ResultTable from "./ResultTable";

const STARTERS = [
  "Top 10 khách hàng theo doanh thu",
  "Doanh thu theo công ty",
  "Doanh thu tháng 3 năm 2025",
  "5 sản phẩm bán chạy nhất",
  "Doanh thu của công ty An Phát",
  "Doanh thu tháng này",
];

type ChatMessage =
  | { role: "user"; text: string }
  | { role: "assistant"; resp: ChatResponse };

export default function Chat() {
  const [conversationId, setConversationId] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const send = async (m?: string) => {
    const text = (m ?? input).trim();
    if (!text || loading) return;
    setErr("");
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);
    try {
      const resp = await api.chat(text, conversationId);
      setConversationId(resp.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", resp }]);
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setConversationId(undefined);
    setMessages([]);
    setErr("");
    setInput("");
  };

  return (
    <div className="chat">
      <div className="chat-header">
        <div className="chat-title">
          <span className="chat-logo">📊</span>
          <div>
            <div className="chat-title-main">Trợ lý dữ liệu bán hàng</div>
            <div className="chat-title-sub">Hỏi bằng tiếng Việt về doanh thu, khách hàng, sản phẩm…</div>
          </div>
        </div>
        <button className="toolbtn" onClick={reset} disabled={loading || messages.length === 0}>
          ＋ Cuộc trò chuyện mới
        </button>
      </div>

      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && (
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
              <AssistantBubble resp={m.resp} />
            </div>
          ),
        )}

        {loading && (
          <div className="chat-row bot">
            <div className="chat-avatar">🤖</div>
            <div className="chat-bubble bot">
              <div className="chat-typing">
                <span></span>
                <span></span>
                <span></span>
              </div>
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
  );
}

function AssistantBubble({ resp }: { resp: ChatResponse }) {
  const hasRows = resp.needs_sql && resp.row_count > 0;
  const emptyData = resp.needs_sql && !resp.error && resp.row_count === 0;
  return (
    <div className={`chat-bubble bot${resp.error ? " has-error" : ""}`}>
      <div className="chat-answer">{resp.answer}</div>

      {emptyData && <div className="chat-empty">📭 Không có dòng dữ liệu nào cho yêu cầu này.</div>}

      {hasRows && (
        <>
          <BarChart columns={resp.columns} rows={resp.rows} />
          <ResultTable columns={resp.columns} rows={resp.rows} />
        </>
      )}

      <TechDetails resp={resp} />
    </div>
  );
}

function TechDetails({ resp }: { resp: ChatResponse }) {
  const copySql = () => {
    if (resp.sql) navigator.clipboard?.writeText(resp.sql);
  };
  const chips: [string, string][] = [
    ["intent", resp.intent],
    ...(resp.tables_used.length ? ([["bảng", resp.tables_used.join(", ")]] as [string, string][]) : []),
    ...(resp.metrics_used.length ? ([["chỉ số", resp.metrics_used.join(", ")]] as [string, string][]) : []),
    ...(resp.filters_used.length ? ([["bộ lọc", resp.filters_used.join(", ")]] as [string, string][]) : []),
    ...(resp.repaired ? ([["đã sửa SQL", "có"]] as [string, string][]) : []),
    ...(resp.llm_model ? ([["mô hình", resp.llm_model]] as [string, string][]) : []),
  ];
  const total = resp.timings_ms?.total;
  return (
    <details className="chat-tech">
      <summary>Chi tiết kỹ thuật</summary>
      <div className="chat-tech-body">
        {resp.sql && (
          <div className="chat-sql-block">
            <div className="chat-sql-head">
              <span>SQL</span>
              <button className="chat-linkbtn" onClick={copySql}>
                ⧉ Sao chép
              </button>
            </div>
            <pre className="skill chat-sql">{resp.sql}</pre>
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
        {resp.validation_warnings.length > 0 && (
          <div className="chat-warn">⚠ {resp.validation_warnings.join(" • ")}</div>
        )}
        {resp.validation_errors.length > 0 && (
          <div className="chat-warn err">✕ {resp.validation_errors.join(" • ")}</div>
        )}
        {resp.error && <div className="chat-warn err">Lỗi: {resp.error}</div>}
      </div>
    </details>
  );
}
