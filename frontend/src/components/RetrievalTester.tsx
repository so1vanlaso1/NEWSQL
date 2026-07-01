import { type CSSProperties, useState } from "react";
import { api } from "../api";
import type { ResolvedContext } from "../types";

const SAMPLES = [
  "Top 10 khách hàng có doanh thu cao nhất",
  "doanh thu theo công ty",
  "sản phẩm bán chạy nhất ngành hàng Sữa",
  "tỉ lệ viếng thăm thành công theo nhân viên",
  "Công ty FMCG An Phát",
  "hàng trả về nhiều nhất theo sản phẩm",
];

function splitTables(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

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

export default function RetrievalTester() {
  const [query, setQuery] = useState<string>("");
  const [pinned, setPinned] = useState<string>("");
  const [ctx, setCtx] = useState<ResolvedContext | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);

  const run = async (q?: string) => {
    const text = (q ?? query).trim();
    if (!text) return;
    if (q !== undefined) setQuery(q);
    setLoading(true);
    setErr("");
    try {
      setCtx(await api.retrieve(text, splitTables(pinned)));
    } catch (e: any) {
      setErr(e.message || String(e));
      setCtx(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="form" style={{ maxWidth: 920 }}>
      <div style={{ fontSize: 12, ...muted }}>
        Phase 3/4 retrieval tester — type a Vietnamese question and inspect the resolved
        context (tables, metrics, joins, matched entities) that a later LLM call would receive.
      </div>

      <label>Câu hỏi (Vietnamese query)</label>
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="ví dụ: Top 10 khách hàng có doanh thu cao nhất tháng gần nhất"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
        }}
      />

      <label>Pinned tables (follow-up refine — optional, comma/space separated)</label>
      <input
        type="text"
        value={pinned}
        onChange={(e) => setPinned(e.target.value)}
        placeholder="khach_hang, don_hang_ban, chi_tiet_don_hang_ban"
      />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        <button className="toolbtn primary" onClick={() => run()} disabled={loading || !query.trim()}>
          {loading ? "Retrieving…" : "Retrieve"}
        </button>
        {SAMPLES.map((s) => (
          <button key={s} className="chip" onClick={() => run(s)} disabled={loading}>
            {s}
          </button>
        ))}
      </div>

      {err && <div className="notice err">{err} — is the backend running on :8000 with the embedder loaded?</div>}

      {ctx && <ResultView ctx={ctx} />}
    </div>
  );
}

function ResultView({ ctx }: { ctx: ResolvedContext }) {
  return (
    <div>
      <div style={{ ...box, display: "flex", gap: 18, flexWrap: "wrap" }}>
        <span style={muted}>dialect <b style={{ color: "var(--text)" }}>{ctx.dialect}</b></span>
        <span style={muted}>final tables <b style={{ color: "var(--text)" }}>{ctx.final_tables.length}</b></span>
        <span style={muted}>metrics <b style={{ color: "var(--text)" }}>{ctx.metrics.length}</b></span>
        <span style={muted}>joins <b style={{ color: "var(--text)" }}>{ctx.joins.length}</b></span>
        <span style={muted}>values <b style={{ color: "var(--text)" }}>{ctx.matched_values.length}</b></span>
        {ctx.pinned_tables.length > 0 && (
          <span style={muted}>pinned <b style={{ color: "var(--accent)" }}>{ctx.pinned_tables.join(", ")}</b></span>
        )}
        {ctx.debug?.temporal_cue && <span style={{ color: "var(--amber)" }}>temporal cue</span>}
      </div>

      {ctx.matched_values.length > 0 && (
        <div style={box}>
          <h3 style={h3}>Matched entities</h3>
          {ctx.matched_values.map((v, i) => (
            <div key={i} style={{ ...mono, marginBottom: 4 }}>
              <b>{v.value}</b> → {v.table}.{v.column}
              {v.id_value ? ` (${v.id_column}=${v.id_value})` : ""}{" "}
              <span style={muted}>via “{v.matched_alias}” [{v.match_kind}]</span>
            </div>
          ))}
        </div>
      )}

      {ctx.metrics.length > 0 && (
        <div style={box}>
          <h3 style={h3}>Metrics</h3>
          {ctx.metrics.map((m) => (
            <div key={m.metric} style={{ marginBottom: 8 }}>
              <div>
                <b style={mono}>{m.metric}</b> <span style={muted}>({m.score.toFixed(3)})</span>
              </div>
              <div style={{ ...mono, color: "var(--green)" }}>{m.formula}</div>
              {m.required_joins.length > 0 && (
                <div style={{ ...mono, ...muted }}>{m.required_joins.join("; ")}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {ctx.joins.length > 0 && (
        <div style={box}>
          <h3 style={h3}>Allowed joins</h3>
          {ctx.joins.map((j, i) => (
            <div key={i} style={{ ...mono, marginBottom: 3 }}>
              {j.condition} <span style={muted}>[{j.source}]</span>
            </div>
          ))}
          {ctx.debug?.unreachable_tables?.length > 0 && (
            <div className="notice err" style={{ marginTop: 8 }}>
              unreachable: {ctx.debug.unreachable_tables.join(", ")}
            </div>
          )}
        </div>
      )}

      {ctx.tables.length > 0 && (
        <div style={box}>
          <h3 style={h3}>Tables &amp; columns</h3>
          {ctx.tables.map((t) => (
            <div key={t.table} style={{ marginBottom: 10 }}>
              <div>
                <b style={mono}>{t.table}</b>{" "}
                <span style={muted}>{t.meaning_en || t.meaning}</span>
                {t.reason && <span style={{ ...muted, fontSize: 11 }}> — {t.reason}</span>}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {t.columns.map((c) => (
                  <span
                    key={c.column}
                    title={c.meaning}
                    style={{
                      ...mono,
                      padding: "2px 6px",
                      borderRadius: 4,
                      border: "1px solid var(--border)",
                      color: c.is_key ? "var(--accent)" : "var(--muted)",
                    }}
                  >
                    {c.column}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {ctx.columns.length > 0 && (
        <div style={box}>
          <h3 style={h3}>Focus columns</h3>
          <div style={{ ...mono }}>
            {ctx.columns.map((c) => `${c.table}.${c.column}`).join("  ·  ")}
          </div>
        </div>
      )}

      <details style={{ ...box }}>
        <summary style={{ cursor: "pointer", ...muted }}>Rules &amp; debug</summary>
        <div style={{ marginTop: 8 }}>
          {ctx.rules.map((r) => (
            <div key={r.section} style={{ marginBottom: 6 }}>
              <b style={{ fontSize: 12 }}>{r.title || r.section}</b>
              {r.content && <div style={{ ...muted, fontSize: 12 }}>{r.content}</div>}
              {r.items.length > 0 && (
                <ul style={{ margin: "4px 0", paddingLeft: 18, ...muted, fontSize: 12 }}>
                  {r.items.map((it, i) => <li key={i}>{it}</li>)}
                </ul>
              )}
            </div>
          ))}
          <pre className="skill" style={{ marginTop: 8 }}>{JSON.stringify(ctx.debug, null, 2)}</pre>
        </div>
      </details>
    </div>
  );
}
