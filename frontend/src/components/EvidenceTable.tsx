import type { EvidenceItem } from "../types";
import { t } from "../i18n";
import ResultTable from "./ResultTable";

export default function EvidenceTable({ evidence }: { evidence: EvidenceItem }) {
  const copy = (text?: string | null) => text && navigator.clipboard?.writeText(text);
  const columns = Array.isArray(evidence?.columns) ? evidence.columns : [];
  const rows = Array.isArray(evidence?.rows) ? evidence.rows : [];
  const status = evidence?.status || "unknown";
  return (
    <div className={`evidence-block evidence-${status}`}>
      <div className="evidence-title">
        <span>{evidence?.title || evidence?.evidence_id || t.analytic.evidence}</span>
        {status !== "success" && <span className="evidence-status">{status}</span>}
      </div>
      {evidence?.purpose && <div className="evidence-purpose">{evidence.purpose}</div>}
      {rows.length > 0 && columns.length > 0 ? (
        <ResultTable columns={columns} rows={rows} />
      ) : (
        <div className="chat-empty">{t.analytic.noData}</div>
      )}
      {evidence?.sql && (
        <details className="evidence-sql">
          <summary>{t.analytic.sql}</summary>
          <div className="chat-sql-head">
            <span>{evidence.task_id || evidence.evidence_id}</span>
            <button className="chat-linkbtn" onClick={() => copy(evidence.sql)}>{t.analytic.copy}</button>
          </div>
          <pre className="skill chat-sql">{evidence.sql}</pre>
        </details>
      )}
    </div>
  );
}
