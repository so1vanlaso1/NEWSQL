import { isMoneyCol } from "./ResultTable";

const VN = new Intl.NumberFormat("vi-VN");

function isNum(v: any): boolean {
  return typeof v === "number" && !Number.isNaN(v);
}

/**
 * Renders a horizontal bar chart ONLY when the result has a clean single-metric shape:
 * exactly one numeric column + one label column and 2..20 rows. Otherwise renders
 * nothing (the table already covers the data).
 */
export default function BarChart({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, any>[];
}) {
  if (rows.length < 2 || rows.length > 20) return null;

  const numericCols = columns.filter(
    (c) => !c.endsWith("_id") && rows.some((r) => isNum(r[c])) && rows.every((r) => r[c] === null || isNum(r[c])),
  );
  if (numericCols.length !== 1) return null;
  const metric = numericCols[0];

  const labelCol =
    columns.find((c) => c.startsWith("ten_")) ||
    columns.find((c) => c !== metric && rows.every((r) => typeof r[c] === "string"));
  if (!labelCol) return null;

  const data = rows
    .map((r) => ({ label: String(r[labelCol] ?? "—"), value: isNum(r[metric]) ? (r[metric] as number) : 0 }))
    .filter((d) => d.value > 0);
  if (data.length < 2) return null;

  const max = Math.max(...data.map((d) => d.value));
  const money = isMoneyCol(metric);
  const fmtVal = (v: number) => (money ? `${VN.format(v)} ₫` : VN.format(v));

  const rowH = 26;
  const gap = 8;
  const labelW = 150;
  const barMax = 260;
  const valueW = 120;
  const width = labelW + barMax + valueW;
  const height = data.length * (rowH + gap);

  return (
    <div className="chat-chart">
      <div className="chat-chart-title">Biểu đồ: {metric}</div>
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Biểu đồ ${metric}`}>
        {data.map((d, i) => {
          const y = i * (rowH + gap);
          const w = Math.max(2, (d.value / max) * barMax);
          return (
            <g key={i} transform={`translate(0, ${y})`}>
              <text x={labelW - 8} y={rowH / 2} dominantBaseline="middle" textAnchor="end" className="chat-chart-label">
                {d.label.length > 22 ? d.label.slice(0, 21) + "…" : d.label}
              </text>
              <rect x={labelW} y={2} width={w} height={rowH - 4} rx={4} className="chat-chart-bar" />
              <text x={labelW + w + 6} y={rowH / 2} dominantBaseline="middle" className="chat-chart-value">
                {fmtVal(d.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
