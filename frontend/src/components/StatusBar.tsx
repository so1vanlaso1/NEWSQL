import { useState } from "react";
import { api } from "../api";
import type { Status } from "../types";

interface Props {
  status: Status | null;
  onChanged: () => void;
  onError: (msg: string) => void;
}

export default function StatusBar({ status, onChanged, onError }: Props) {
  const [busy, setBusy] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<any>) {
    setBusy(label);
    try {
      await fn();
      onChanged();
    } catch (e: any) {
      onError(e.message || String(e));
    } finally {
      setBusy(null);
    }
  }

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
      <span className="stat">pending <b>{pending}</b></span>
      <span className="stat">errors <b style={errors ? { color: "var(--red)" } : {}}>{errors}</b></span>
      <span className="stat">dialect <b>{status?.dialect ?? "?"}</b></span>
      <span className="spacer" />
      <button className="toolbtn" disabled={!!busy} onClick={() => run("seed", () => api.seed(false))}>
        {busy === "seed" ? "Seeding…" : "Seed"}
      </button>
      <button className="toolbtn" disabled={!!busy} onClick={() => run("reembed", () => api.rebuildEmbeddings())}>
        {busy === "reembed" ? "Embedding…" : "Rebuild embeddings"}
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
