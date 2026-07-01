import type { Entry } from "../types";

interface Props {
  entries: Entry[];
  selectedId?: string;
  onSelect: (e: Entry) => void;
}

export default function EntryList({ entries, selectedId, onSelect }: Props) {
  if (entries.length === 0) {
    return <div className="empty">No entries. Seed the store or create one.</div>;
  }
  return (
    <div className="list">
      {entries.map((e) => (
        <div
          key={e.id}
          className={`row ${e.id === selectedId ? "sel" : ""}`}
          onClick={() => onSelect(e)}
          title={e.embed_error || e.name}
        >
          <span className="rtype">{e.type}</span>
          <span className="rid">{e.name || e.id}</span>
          <span className={`badge ${e.embed_status}`}>{e.embed_status.replace("_", " ")}</span>
        </div>
      ))}
    </div>
  );
}
