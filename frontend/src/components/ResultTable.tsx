import { useMemo } from "react";

// Money-like columns get VND formatting. Anchored at the end so date columns such as
// `ngay_giao` (contains "gia") never match; only numeric values are ever formatted anyway.
const MONEY_RE = /(doanh_thu|thanh_tien|tong_tien|don_gia|gia_ban|revenue|amount|_tien|_gia)$/i;
const VN = new Intl.NumberFormat("vi-VN");
const DISPLAY_CAP = 50;

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
  const shown = rows.slice(0, DISPLAY_CAP);
  const hidden = rows.length - shown.length;
  const numericByCol = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const c of columns) m[c] = rows.some((r) => isNum(r[c]));
    return m;
  }, [columns, rows]);

  const exportCsv = () => {
    const blob = new Blob([`﻿${toCsv(columns, rows)}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ket_qua.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!columns.length) return null;

  return (
    <div className="chat-tablewrap">
      <div className="chat-table-scroll">
        <table className="chat-table">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} className={numericByCol[c] ? "num" : ""}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                {columns.map((c) => (
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
        <span>
          {rows.length} dòng{hidden > 0 ? ` (hiển thị ${DISPLAY_CAP} dòng đầu)` : ""}
        </span>
        <button className="chat-linkbtn" onClick={exportCsv}>
          ⬇ Xuất CSV
        </button>
      </div>
    </div>
  );
}

export { fmt as formatCell, isMoneyCol };
