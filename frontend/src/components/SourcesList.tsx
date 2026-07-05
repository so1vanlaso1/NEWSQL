import { t } from "../i18n";

export default function SourcesList({ sources }: { sources: Record<string, any>[] }) {
  if (!sources?.length) {
    return <div className="sources-empty">{t.analytic.noSources}</div>;
  }
  return (
    <ol className="sources-list">
      {sources.map((s, i) => (
        <li key={s.url || i}>
          <span className="source-index">[{s.n ?? i + 1}]</span>{" "}
          {s.url ? (
            <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
          ) : (
            <span>{s.title || "Nguồn"}</span>
          )}
          {s.retrieved_at && <span className="source-date"> · {s.retrieved_at}</span>}
        </li>
      ))}
    </ol>
  );
}

