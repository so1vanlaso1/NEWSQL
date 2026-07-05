import { useEffect, useState } from "react";
import { api } from "../api";
import type { Health, Status } from "../types";

interface Props {
  status: Status | null;
  onChanged: () => void;
  onError: (msg: string) => void;
}

// A traffic light: true = green (up), false = red (down), null/undefined = grey (n/a / off).
function Light({ label, ok, title }: { label: string; ok?: boolean | null; title?: string }) {
  const cls = ok === true ? "up" : ok === false ? "down" : "off";
  return (
    <span className="stat health-light" title={title}>
      <span className={`light-dot ${cls}`} /> {label}
    </span>
  );
}

export default function StatusBar({ status, onChanged, onError }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [kbVersion, setKbVersion] = useState<number | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  async function run(label: string, fn: () => Promise<any>) {
    setBusy(label);
    try {
      await fn();
      onChanged();
      refreshVersion();
    } catch (e: any) {
      onError(e.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function refreshVersion() {
    try {
      setKbVersion((await api.kbVersion()).kb_version);
    } catch {
      /* leave the last known version */
    }
  }

  async function refreshHealth() {
    try {
      setHealth(await api.health());
    } catch {
      /* leave the last known health */
    }
  }

  // Re-read the KB version + health whenever the store changes (status reloads drive this).
  useEffect(() => {
    refreshVersion();
    refreshHealth();
  }, [status]);

  const dev = status?.embedder.device ?? "?";
  const isCpu = dev === "cpu";
  const pending = status?.entries.by_status?.pending ?? 0;
  const errors = status?.entries.by_status?.error ?? 0;

  return (
    <div className="statusbar">
      <h1>🧠 Knowledge Storage</h1>
      <span className="stat">
        model <b>{status?.embedder.model_name ?? "…"}</b>{" "}
        <span className={`device ${isCpu ? "cpu" : ""}`}>({dev}, dim {status?.embedder.dim ?? "?"})</span>
      </span>
      <span className="stat">index <b>{status?.index.size ?? 0}</b></span>
      <span className="stat" title="Knowledge-base version — bumps on every save/delete; edits are live on the next question.">
        KB <b>v{kbVersion ?? "?"}</b>
      </span>
      <span className="stat">pending <b style={pending ? { color: "var(--red)" } : {}}>{pending}</b></span>
      <span className="stat">errors <b style={errors ? { color: "var(--red)" } : {}}>{errors}</b></span>
      <span className="stat">dialect <b>{status?.dialect ?? "?"}</b></span>
      <Light label="LLM" ok={health?.llm?.reachable}
             title={`Model: ${health?.llm?.model ?? "?"}${health?.llm?.latency_ms != null ? ` · ${health.llm.latency_ms}ms` : ""}`} />
      <Light label="embedder" ok={health?.embedder?.ok}
             title={`${health?.embedder?.model ?? "?"} (${health?.embedder?.device ?? "?"})`} />
      <Light label="search"
             ok={health?.search?.enabled ? health?.search?.reachable : null}
             title={health?.search?.enabled ? `SearxNG: ${health?.search?.url ?? "?"}` : "Tìm kiếm web đang tắt"} />
      <span className="spacer" />
      <button className="toolbtn" disabled={!!busy} onClick={() => run("seed", () => api.seed(false))}>
        {busy === "seed" ? "Seeding…" : "Seed"}
      </button>
      {pending > 0 && (
        <button className="toolbtn" disabled={!!busy} onClick={() => run("pending", () => api.embedPending())}>
          {busy === "pending" ? "Embedding…" : `Embed pending (${pending})`}
        </button>
      )}
      <button className="toolbtn" disabled={!!busy} onClick={() => run("reembed", () => api.rebuildEmbeddings())}>
        {busy === "reembed" ? "Embedding…" : "Rebuild embeddings"}
      </button>
      <button className="toolbtn" disabled={!!busy} onClick={() => run("sync", () => api.syncValues())}
              title="Re-sample distinct entity values from sales.db">
        {busy === "sync" ? "Syncing…" : "Sync values"}
      </button>
      <button className="toolbtn" disabled={!!busy} onClick={() => run("skill", () => api.rebuildSkillMd())}>
        {busy === "skill" ? "Writing…" : "Write skill.md"}
      </button>
      <button className="toolbtn" disabled={!!busy} onClick={() => run("export", () => api.exportDocs())}>
        {busy === "export" ? "Exporting…" : "Export docs"}
      </button>
    </div>
  );
}
