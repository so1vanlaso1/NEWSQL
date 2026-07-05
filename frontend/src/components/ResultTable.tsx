import { useMemo, useState } from "react";
import { t } from "../i18n";

const MONEY_RE = /(doanh_thu|thanh_tien|tong_tien|don_gia|gia_ban|revenue|amount|_tien|_gia)$/i;
const VN = new Intl.NumberFormat("vi-VN");
const PAGE_SIZE = 50;

function isMoneyCol(name: string): boolean {
  return MONEY_RE.test(name) || name === "gia" || name === "tien";
}

function isNum(v: any): boolean {
  return typeof v === "number" && !Number.isNaN(v);
}

function fmt(col: string, v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (isNum(v)) {
    if (isMoneyCol(col)) return `${VN.format(v)} ₫`;
    return VN.format(v);
  }
  return String(v);
}

function toCsv(columns: string[], rows: Record<string, any>[]): string {
  const esc = (v: any) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map(esc).join(",");
  const body = rows.map((r) => columns.map((c) => esc(r[c])).join(",")).join("\n");
  return `${head}\n${body}`;
}

export default function ResultTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, any>[];
}) {
  const [page, setPage] = useState(0);
  const safeColumns = Array.isArray(columns) ? columns : [];
  const safeRows = Array.isArray(rows) ? rows : [];
  const pageCount = Math.max(1, Math.ceil(safeRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const shown = safeRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
  const numericByCol = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const c of safeColumns) m[c] = safeRows.some((r) => isNum(r[c]));
    return m;
  }, [safeColumns, safeRows]);

  const exportCsv = () => {
    const blob = new Blob([`\uFEFF${toCsv(safeColumns, safeRows)}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ket_qua.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!safeColumns.length) return null;

  return (
    <div className="chat-tablewrap">
      <div className="chat-table-scroll">
        <table className="chat-table">
          <thead>
            <tr>
              {safeColumns.map((c) => (
                <th key={c} className={numericByCol[c] ? "num" : ""}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={`${safePage}-${i}`}>
                {safeColumns.map((c) => (
                  <td key={c} className={numericByCol[c] ? "num" : ""}>
                    {fmt(c, r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="chat-table-foot">
        <span>{safeRows.length} {t.table.rows}</span>
        {safeRows.length > PAGE_SIZE && (
          <span className="table-pager">
            <button className="chat-linkbtn" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
              {t.table.previous}
            </button>
            <span>{t.table.page} {safePage + 1}/{pageCount}</span>
            <button className="chat-linkbtn" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>
              {t.table.next}
            </button>
          </span>
        )}
        <button className="chat-linkbtn" onClick={exportCsv}>
          {t.analytic.csv}
        </button>
      </div>
    </div>
  );
}

export { fmt as formatCell, isMoneyCol };
