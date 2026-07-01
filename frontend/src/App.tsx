import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Chat from "./components/Chat";
import ChatPlanTester from "./components/ChatPlanTester";
import EntryForm from "./components/EntryForm";
import EntryList from "./components/EntryList";
import RetrievalTester from "./components/RetrievalTester";
import SkillMdPreview from "./components/SkillMdPreview";
import StatusBar from "./components/StatusBar";
import { ENTRY_TYPES, type Entry, type EntryType, type SaveResult, type Status } from "./types";

type Selection =
  | { mode: "edit"; entry: Entry }
  | { mode: "new"; type: EntryType }
  | null;

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [q, setQ] = useState<string>("");
  const [sel, setSel] = useState<Selection>(null);
  const [newType, setNewType] = useState<EntryType>("metric");
  const [tab, setTab] = useState<"chat" | "entries" | "skill" | "retrieval" | "chat-plan">("chat");
  const [err, setErr] = useState<string>("");

  const reloadStatus = useCallback(async () => {
    try {
      setStatus(await api.status());
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }, []);

  const reloadEntries = useCallback(async () => {
    try {
      setEntries(await api.listEntries({ type: typeFilter || undefined, q: q || undefined }));
      setErr("");
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }, [typeFilter, q]);

  useEffect(() => {
    reloadStatus();
  }, [reloadStatus]);
  useEffect(() => {
    reloadEntries();
  }, [reloadEntries]);

  const onSaved = (r: SaveResult) => {
    setSel({ mode: "edit", entry: r.entry });
    reloadEntries();
    reloadStatus();
  };
  const onDeleted = () => {
    setSel(null);
    reloadEntries();
    reloadStatus();
  };

  return (
    <div className="app">
      <StatusBar status={status} onChanged={() => { reloadStatus(); reloadEntries(); }} onError={setErr} />

      <div className="tabbar">
        <button className={`chip ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>💬 Chat</button>
        <button className={`chip ${tab === "entries" ? "active" : ""}`} onClick={() => setTab("entries")}>Entries</button>
        <button className={`chip ${tab === "skill" ? "active" : ""}`} onClick={() => setTab("skill")}>skill.md preview</button>
        <button className={`chip ${tab === "retrieval" ? "active" : ""}`} onClick={() => setTab("retrieval")}>Retrieval</button>
        <button className={`chip ${tab === "chat-plan" ? "active" : ""}`} onClick={() => setTab("chat-plan")}>Chat Plan</button>
      </div>

      {err && <div className="notice err" style={{ margin: "8px 16px" }}>{err} — is the backend running on :8000?</div>}

      {tab === "chat" ? (
        <Chat />
      ) : tab === "skill" ? (
        <div className="detail"><SkillMdPreview /></div>
      ) : tab === "retrieval" ? (
        <div className="detail"><RetrievalTester /></div>
      ) : tab === "chat-plan" ? (
        <div className="detail"><ChatPlanTester /></div>
      ) : (
        <div className="body">
          <div className="sidebar">
            <div className="filters">
              <button className={`chip ${typeFilter === "" ? "active" : ""}`} onClick={() => setTypeFilter("")}>all</button>
              {ENTRY_TYPES.map((t) => (
                <button key={t} className={`chip ${typeFilter === t ? "active" : ""}`} onClick={() => setTypeFilter(t)}>{t}</button>
              ))}
              <input type="text" placeholder="search id / name / text…" value={q} onChange={(e) => setQ(e.target.value)} />
              <select value={newType} onChange={(e) => setNewType(e.target.value as EntryType)}
                      style={{ background: "var(--panel2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "6px" }}>
                {ENTRY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <button className="toolbtn primary" onClick={() => setSel({ mode: "new", type: newType })}>＋ New</button>
            </div>
            <EntryList
              entries={entries}
              selectedId={sel?.mode === "edit" ? sel.entry.id : undefined}
              onSelect={(e) => setSel({ mode: "edit", entry: e })}
            />
          </div>

          <div className="detail">
            {sel === null ? (
              <div className="empty">Select an entry to edit, or create a new one.</div>
            ) : sel.mode === "new" ? (
              <EntryForm
                type={sel.type}
                initialBody={{}}
                initialEnabled={true}
                onSaved={onSaved}
                onDeleted={onDeleted}
                onCancel={() => setSel(null)}
              />
            ) : (
              <EntryForm
                type={sel.entry.type}
                entryId={sel.entry.id}
                initialBody={sel.entry.body}
                initialEnabled={sel.entry.enabled}
                onSaved={onSaved}
                onDeleted={onDeleted}
                onCancel={() => setSel(null)}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
