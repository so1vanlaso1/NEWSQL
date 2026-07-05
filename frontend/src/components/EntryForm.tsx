import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { FIELD_SPECS, type Entry, type EntryType, type HistoryRow, type SaveResult } from "../types";

interface Props {
  type: EntryType;
  entryId?: string;          // present when editing
  initialBody: Record<string, any>;
  initialEnabled: boolean;
  onSaved: (r: SaveResult) => void;
  onDeleted: (id: string) => void;
  onCancel: () => void;
}

function bodyToForm(type: EntryType, body: Record<string, any>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of FIELD_SPECS[type]) {
    const v = body[f.key];
    if (f.kind === "list") out[f.key] = Array.isArray(v) ? v.join("\n") : "";
    else if (f.kind === "json") out[f.key] = JSON.stringify(v ?? {}, null, 2);
    else out[f.key] = v == null ? "" : String(v);
  }
  return out;
}

function formToBody(type: EntryType, form: Record<string, string>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of FIELD_SPECS[type]) {
    const raw = form[f.key] ?? "";
    if (f.kind === "list") {
      out[f.key] = raw.split("\n").map((s) => s.trim()).filter(Boolean);
    } else if (f.kind === "json") {
      out[f.key] = JSON.parse(raw || "{}"); // may throw -> caught by caller
    } else {
      out[f.key] = raw;
    }
  }
  return out;
}

function parseJsonArray(text: string): any[] {
  try {
    const v = JSON.parse(text || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function stringifySteps(steps: any[]): string {
  return JSON.stringify(steps, null, 2);
}

const SHAPES = ["kpi", "by_dimension", "trend", "top_n"];

export default function EntryForm(props: Props) {
  const { type, entryId, initialBody, initialEnabled } = props;
  const isEdit = !!entryId;
  const [form, setForm] = useState<Record<string, string>>(() => bodyToForm(type, initialBody));
  const [enabled, setEnabled] = useState(initialEnabled);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; msg: string } | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [metrics, setMetrics] = useState<Entry[]>([]);
  const [dimensions, setDimensions] = useState<Entry[]>([]);
  const [templates, setTemplates] = useState<Entry[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [dryRun, setDryRun] = useState<string>("");

  useEffect(() => {
    setForm(bodyToForm(type, initialBody));
    setEnabled(initialEnabled);
    setNotice(null);
    setHistory(null);
    setDryRun("");
  }, [type, entryId, initialBody, initialEnabled]);

  useEffect(() => {
    if (type !== "playbook") return;
    let cancelled = false;
    (async () => {
      try {
        const [m, d, p] = await Promise.all([
          api.listEntries({ type: "metric" }),
          api.listEntries({ type: "dimension" }),
          api.listEntries({ type: "playbook" }),
        ]);
        if (!cancelled) {
          setMetrics(m);
          setDimensions(d);
          setTemplates(p);
          setTemplateId((p.find((x) => x.id !== entryId)?.id) || "");
        }
      } catch {
        if (!cancelled) {
          setMetrics([]);
          setDimensions([]);
          setTemplates([]);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [type, entryId]);

  async function toggleHistory() {
    if (history !== null) {
      setHistory(null);
      return;
    }
    if (!entryId) return;
    setBusy(true);
    try {
      setHistory((await api.entryHistory(entryId)).history);
    } catch (e: any) {
      setNotice({ ok: false, msg: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function restore(historyId: number) {
    if (!entryId) return;
    if (!confirm(`Restore ${entryId} to version #${historyId}?`)) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.restoreEntry(entryId, historyId);
      setNotice({ ok: r.embed_status !== "error", msg: `Restored (${r.embed_status}).` });
      setHistory((await api.entryHistory(entryId)).history);
      props.onSaved(r);
    } catch (e: any) {
      setNotice({ ok: false, msg: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  const specs = useMemo(() => FIELD_SPECS[type], [type]);

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const body = formToBody(type, form);
      const payload = { type, body, enabled };
      const r = isEdit ? await api.updateEntry(entryId!, payload) : await api.createEntry(payload);
      const s = r.embed_status;
      const msg = r.embedded
        ? `Saved & embedded (${s}).`
        : s === "embedded"
        ? "Saved (unchanged, no re-embed)."
        : `Saved (${s}${r.embed_error ? ": " + r.embed_error : ""}).`;
      setNotice({ ok: s !== "error", msg });
      props.onSaved(r);
    } catch (e: any) {
      setNotice({ ok: false, msg: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function reembed() {
    if (!entryId) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.reembed(entryId);
      setNotice({ ok: r.embed_status !== "error", msg: `Re-embedded (${r.embed_status}).` });
      props.onSaved(r);
    } catch (e: any) {
      setNotice({ ok: false, msg: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!entryId) return;
    if (!confirm(`Delete ${entryId}?`)) return;
    setBusy(true);
    try {
      await api.deleteEntry(entryId);
      props.onDeleted(entryId);
    } catch (e: any) {
      setNotice({ ok: false, msg: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  function applyTemplate() {
    const tpl = templates.find((x) => x.id === templateId);
    if (!tpl) return;
    const body = { ...tpl.body };
    const base = String(body.playbook || "playbook").replace(/_copy\d*$/, "");
    body.playbook = `${base}_copy`;
    setForm(bodyToForm("playbook", body));
    setEnabled(true);
    setNotice({ ok: true, msg: "Đã tạo bản nháp từ mẫu. Kiểm tra slug rồi lưu để tạo playbook mới." });
  }

  async function runDryRun() {
    setBusy(true);
    setDryRun("");
    try {
      const aliases = (form.aliases || "").split("\n").map((s) => s.trim()).filter(Boolean);
      const message = aliases[0] || form.use_when || `Phân tích ${form.playbook || "playbook"}`;
      const r = await api.analysisPlan(message);
      const ctx = r.analytic_context as any;
      const tables = ctx?.schema_context?.final_tables || [];
      const playbooks = (ctx?.playbooks || []).map((p: any) => p.playbook).filter(Boolean);
      setDryRun([
        `mode: ${r.mode}`,
        `tables: ${tables.length ? tables.join(", ") : "-"}`,
        `playbooks: ${playbooks.length ? playbooks.join(", ") : "-"}`,
        r.note ? `note: ${r.note}` : "",
      ].filter(Boolean).join("\n"));
    } catch (e: any) {
      setDryRun(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function setSteps(steps: any[]) {
    setForm({ ...form, diagnostic_steps: stringifySteps(steps) });
  }

  return (
    <div className="form">
      <div className="formhead">
        <h2>{isEdit ? "Edit" : "New"} {type}</h2>
        {entryId && <span className="mono" style={{ color: "var(--muted)", fontSize: 12 }}>{entryId}</span>}
      </div>

      {specs.map((f) => {
        const locked = busy || (isEdit && !!f.lockOnEdit);
        if (type === "playbook" && f.key === "diagnostic_steps") {
          return (
            <div key={f.key}>
              <label>{f.label}</label>
              <PlaybookStepEditor
                steps={parseJsonArray(form[f.key] || "[]")}
                metrics={metrics}
                dimensions={dimensions}
                disabled={busy}
                onChange={setSteps}
              />
            </div>
          );
        }
        return (
          <div key={f.key}>
            <label>{f.label}{f.lockOnEdit ? " (key)" : ""}</label>
            {f.kind === "textarea" || f.kind === "json" || f.kind === "list" ? (
              <textarea
                value={form[f.key] ?? ""}
                disabled={locked}
                placeholder={f.kind === "list" ? "one per line" : f.kind === "json" ? "{ }" : ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                style={f.kind === "json" ? { fontFamily: "ui-monospace, monospace" } : undefined}
              />
            ) : (
              <input
                type="text"
                value={form[f.key] ?? ""}
                disabled={locked}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              />
            )}
          </div>
        );
      })}

      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
        <input type="checkbox" checked={enabled} disabled={busy} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled (disabled entries are removed from the vector index)
      </label>

      {type === "playbook" && (
        <div className="playbook-tools">
          {!isEdit && templates.length > 0 && (
            <>
              <select value={templateId} disabled={busy} onChange={(e) => setTemplateId(e.target.value)}>
                {templates.map((tpl) => (
                  <option key={tpl.id} value={tpl.id}>{tpl.name || tpl.id}</option>
                ))}
              </select>
              <button className="toolbtn" type="button" onClick={applyTemplate} disabled={busy || !templateId}>
                {t.kb.createFromTemplate}
              </button>
            </>
          )}
          <button className="toolbtn" type="button" onClick={runDryRun} disabled={busy}>
            {t.kb.dryRun}
          </button>
        </div>
      )}

      <div className="actions">
        <button className="toolbtn primary" onClick={save} disabled={busy}>
          {busy ? "Working…" : isEdit ? "Save & embed" : "Create & embed"}
        </button>
        {isEdit && <button className="toolbtn" onClick={reembed} disabled={busy}>Re-embed</button>}
        {isEdit && <button className="toolbtn" onClick={toggleHistory} disabled={busy}>
          {history !== null ? "Hide history" : "History"}
        </button>}
        {isEdit && <button className="toolbtn" onClick={remove} disabled={busy}>Delete</button>}
        <button className="toolbtn" onClick={props.onCancel} disabled={busy}>Cancel</button>
      </div>

      {notice && <div className={`notice ${notice.ok ? "ok" : "err"}`}>{notice.msg}</div>}
      {dryRun && <pre className="skill dryrun">{dryRun}</pre>}

      {history !== null && (
        <div className="history" style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 13, color: "var(--muted)" }}>{t.kb.history} (mới nhất trước)</h3>
          {history.length === 0 ? (
            <div className="empty">No history recorded.</div>
          ) : (
            history.map((h) => (
              <div key={h.history_id}
                   style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0",
                            borderBottom: "1px solid var(--border)", fontSize: 12 }}>
                <span className={`badge ${h.action}`}>{h.action}</span>
                <span className="mono" style={{ color: "var(--muted)" }}>{h.changed_at}</span>
                <span className="spacer" style={{ flex: 1 }} />
                <button className="toolbtn" disabled={busy}
                        title={JSON.stringify(h.new_body ?? h.old_body ?? {}, null, 2)}
                        onClick={() => restore(h.history_id)}>
                  {t.kb.restore}
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function optionValue(entry: Entry, key: string): string {
  return String(entry.body?.[key] || entry.name || entry.id);
}

function PlaybookStepEditor({
  steps,
  metrics,
  dimensions,
  disabled,
  onChange,
}: {
  steps: any[];
  metrics: Entry[];
  dimensions: Entry[];
  disabled: boolean;
  onChange: (steps: any[]) => void;
}) {
  const update = (idx: number, patch: Record<string, any>) => {
    const next = steps.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    onChange(next);
  };
  const add = () => onChange([
    ...steps,
    { title: "", purpose: "", metric: "", dimension: "", expected_shape: "kpi", sql_hint: "" },
  ]);
  const remove = (idx: number) => onChange(steps.filter((_, i) => i !== idx));
  const duplicate = (idx: number) => onChange([
    ...steps.slice(0, idx + 1),
    { ...steps[idx], title: `${steps[idx]?.title || "Bước"} copy` },
    ...steps.slice(idx + 1),
  ]);
  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[idx], next[j]] = [next[j], next[idx]];
    onChange(next);
  };

  return (
    <div className="step-editor">
      {steps.length === 0 && <div className="empty inline">Chưa có bước chẩn đoán.</div>}
      {steps.map((step, idx) => (
        <div key={idx} className="step-card">
          <div className="step-card-head">
            <span>Bước {idx + 1}</span>
            <div className="step-card-actions">
              <button className="chat-linkbtn" type="button" disabled={disabled || idx === 0} onClick={() => move(idx, -1)}>{t.kb.moveUp}</button>
              <button className="chat-linkbtn" type="button" disabled={disabled || idx === steps.length - 1} onClick={() => move(idx, 1)}>{t.kb.moveDown}</button>
              <button className="chat-linkbtn" type="button" disabled={disabled} onClick={() => duplicate(idx)}>{t.kb.duplicateStep}</button>
              <button className="chat-linkbtn danger" type="button" disabled={disabled} onClick={() => remove(idx)}>{t.kb.removeStep}</button>
            </div>
          </div>
          <div className="step-grid">
            <label>
              Tiêu đề
              <input type="text" value={step.title || ""} disabled={disabled} onChange={(e) => update(idx, { title: e.target.value })} />
            </label>
            <label>
              Shape
              <select value={step.expected_shape || "kpi"} disabled={disabled} onChange={(e) => update(idx, { expected_shape: e.target.value })}>
                {SHAPES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label>
              Metric
              <select value={step.metric || ""} disabled={disabled} onChange={(e) => update(idx, { metric: e.target.value })}>
                <option value="">-</option>
                {metrics.map((m) => {
                  const v = optionValue(m, "metric");
                  return <option key={m.id} value={v}>{v}</option>;
                })}
              </select>
            </label>
            <label>
              Dimension
              <select value={step.dimension || ""} disabled={disabled} onChange={(e) => update(idx, { dimension: e.target.value })}>
                <option value="">-</option>
                {dimensions.map((d) => {
                  const v = optionValue(d, "dimension");
                  return <option key={d.id} value={v}>{v}</option>;
                })}
              </select>
            </label>
          </div>
          <label>
            Mục đích
            <textarea value={step.purpose || ""} disabled={disabled} onChange={(e) => update(idx, { purpose: e.target.value })} />
          </label>
          <label>
            SQL hint
            <textarea className="sql-hint" value={step.sql_hint || ""} disabled={disabled} onChange={(e) => update(idx, { sql_hint: e.target.value })} />
          </label>
        </div>
      ))}
      <button className="toolbtn" type="button" disabled={disabled} onClick={add}>{t.kb.addStep}</button>
    </div>
  );
}
