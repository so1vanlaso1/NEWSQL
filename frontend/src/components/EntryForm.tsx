import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { FIELD_SPECS, type EntryType, type HistoryRow, type SaveResult } from "../types";

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

export default function EntryForm(props: Props) {
  const { type, entryId, initialBody, initialEnabled } = props;
  const isEdit = !!entryId;
  const [form, setForm] = useState<Record<string, string>>(() => bodyToForm(type, initialBody));
  const [enabled, setEnabled] = useState(initialEnabled);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; msg: string } | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);

  useEffect(() => {
    setForm(bodyToForm(type, initialBody));
    setEnabled(initialEnabled);
    setNotice(null);
    setHistory(null);
  }, [type, entryId, initialBody, initialEnabled]);

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

  return (
    <div className="form">
      <div className="formhead">
        <h2>{isEdit ? "Edit" : "New"} {type}</h2>
        {entryId && <span className="mono" style={{ color: "var(--muted)", fontSize: 12 }}>{entryId}</span>}
      </div>

      {specs.map((f) => {
        const locked = busy || (isEdit && !!f.lockOnEdit);
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

      {history !== null && (
        <div className="history" style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 13, color: "var(--muted)" }}>History (newest first)</h3>
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
                  Restore
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
