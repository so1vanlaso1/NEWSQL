import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import type { ChartSpec, EvidenceItem } from "../types";
import { t } from "../i18n";
import ChartRenderer from "./ChartRenderer";
import ErrorBoundary from "./ErrorBoundary";
import EvidenceTable from "./EvidenceTable";
import SourcesList from "./SourcesList";

export interface AnalyticReportView {
  review_id?: string;
  report_markdown: string;
  evidence: EvidenceItem[];
  charts: ChartSpec[];
  sources: Record<string, any>[];
  caveats: string[];
  follow_up_suggestions: string[];
  analytic_status: string;
}

function statusLabel(status: string): string {
  if (status === "degraded") return t.analytic.degraded;
  if (status === "failed") return t.analytic.failed;
  return t.analytic.complete;
}

export default function AnalyticReport({
  view,
  onFollowUp,
}: {
  view: AnalyticReportView;
  onFollowUp: (m: string) => void;
}) {
  // A partial/streaming view (or a history turn built without one of these fields) can arrive
  // with any array undefined; normalize so a missing array never crashes the render.
  const evidence = view.evidence ?? [];
  const charts = view.charts ?? [];
  const sources = view.sources ?? [];
  const caveats = view.caveats ?? [];
  const followUps = view.follow_up_suggestions ?? [];
  // Web evidence (source_type="web") is provenance for the cited sources — it carries no
  // table, so it is rendered by SourcesList below, never as an empty evidence table here.
  const sqlEvidence = evidence.filter((ev) => ev.source_type !== "web");
  return (
    <div className="analytic-report">
      <div className="analytic-badge">
        <span className="analytic-badge-text">
          {t.analytic.badge} · {statusLabel(view.analytic_status)}
          {view.review_id && <span className="analytic-review-id"> · {view.review_id}</span>}
        </span>
        {view.review_id && <DownloadPdfButton reviewId={view.review_id} />}
      </div>

      {view.report_markdown && (
        <div className="analytic-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{view.report_markdown}</ReactMarkdown>
        </div>
      )}

      {charts.length > 0 && (
        <section className="report-section">
          <h4>{t.analytic.charts}</h4>
          <div className="report-chart-grid">
            {charts.map((chart) => (
              <ErrorBoundary key={chart.chart_id}>
                <ChartRenderer chart={chart} />
              </ErrorBoundary>
            ))}
          </div>
        </section>
      )}

      {sqlEvidence.length > 0 && (
        <section className="report-section">
          <h4>{t.analytic.evidence}</h4>
          <div className="analytic-evidence">
            {sqlEvidence.map((ev) => (
              <ErrorBoundary key={ev.evidence_id}>
                <EvidenceTable evidence={ev} />
              </ErrorBoundary>
            ))}
          </div>
        </section>
      )}

      <section className="report-section">
        <h4>{t.analytic.sources}</h4>
        <SourcesList sources={sources} />
      </section>

      {caveats.length > 0 && (
        <section className="report-section caveats">
          <h4>{t.analytic.caveats}</h4>
          <ul>
            {caveats.map((c) => <li key={c}>{c}</li>)}
          </ul>
        </section>
      )}

      {followUps.length > 0 && (
        <section className="report-section">
          <h4>{t.analytic.followups}</h4>
          <div className="analytic-followups">
            {followUps.map((s) => (
              <button key={s} className="chat-starter followup-chip" onClick={() => onFollowUp(s)}>
                {s}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function DownloadPdfButton({ reviewId }: { reviewId: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const download = async () => {
    if (busy) return;
    setBusy(true);
    setErr(false);
    try {
      await api.downloadReviewPdf(reviewId);
    } catch {
      setErr(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      className="analytic-pdf-btn"
      onClick={download}
      disabled={busy}
      title={err ? t.analytic.pdfError : t.analytic.downloadPdf}
    >
      {busy ? "⏳" : err ? "⚠" : "⬇"} {busy ? t.analytic.pdfBusy : t.analytic.downloadPdf}
    </button>
  );
}

