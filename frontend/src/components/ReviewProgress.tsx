import { t } from "../i18n";

type StepStatus = "active" | "done" | "error" | "skipped";

export interface ReviewProgressState {
  order: string[];
  steps: Record<string, { status: StepStatus; note?: string }>;
  streamText: string;
}

function extractAnswerPreview(raw: string): string {
  const key = raw.indexOf('"answer"');
  if (key === -1) return raw.slice(-500);
  const colon = raw.indexOf(":", key + 8);
  if (colon === -1) return "";
  let i = colon + 1;
  while (i < raw.length && raw[i] !== '"') i++;
  if (i >= raw.length) return "";
  i++;
  let out = "";
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === "\\") {
      const nx = raw[i + 1];
      if (nx === "n") out += "\n";
      else if (nx === "t") out += "\t";
      else if (nx !== undefined) out += nx;
      i += 2;
      continue;
    }
    if (ch === '"') break;
    out += ch;
    i++;
  }
  return out;
}

export default function ReviewProgress({
  progress,
  labels,
  onStop,
}: {
  progress: ReviewProgressState;
  labels: Record<string, string>;
  onStop: () => void;
}) {
  const preview = extractAnswerPreview(progress.streamText || "");
  const active = progress.order.some((k) => progress.steps[k]?.status === "active");
  return (
    <div className="stepper">
      <div className="stepper-head">
        <span className="stepper-title">{t.progress.processing}</span>
        <button className="chat-linkbtn" onClick={onStop}>× {t.progress.stop}</button>
      </div>
      {progress.order.length === 0 && (
        <div className="chat-typing"><span></span><span></span><span></span></div>
      )}
      {progress.order.map((key) => {
        const st = progress.steps[key];
        return (
          <div key={key} className={`step ${st.status}`}>
            <span className="step-icon">
              {st.status === "active" ? <span className="spinner" /> : st.status === "done" ? "✓" : st.status === "error" ? "×" : "–"}
            </span>
            <span className="step-label">{labels[key] ?? key}</span>
            {st.note && <span className="step-note">{st.note}</span>}
          </div>
        );
      })}
      {active && preview && (
        <div className="stream-preview">
          {preview}
          <span className="stream-caret" />
        </div>
      )}
    </div>
  );
}

