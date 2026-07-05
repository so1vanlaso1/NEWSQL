import {
  Bar,
  BarChart as RBarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec } from "../types";
import { t } from "../i18n";

const COLORS = ["#4c8bf5", "#2ecc71", "#f1c40f", "#e67e22", "#9b59b6", "#1abc9c"];
const VN = new Intl.NumberFormat("vi-VN");

function fmtValue(v: any, unit: string): string {
  if (typeof v !== "number" || Number.isNaN(v)) return String(v ?? "");
  return unit === "VND" ? `${VN.format(v)} ₫` : VN.format(v);
}

function tooltipFormatter(value: any, name: any, props: any, unit: string) {
  const label = props?.payload?.name ?? name;
  return [fmtValue(value, unit), label];
}

export default function ChartRenderer({ chart }: { chart: ChartSpec }) {
  const data = Array.isArray(chart?.data) ? chart.data : [];
  const series = Array.isArray(chart?.series) ? chart.series : [];
  if (!chart || chart.type === "none" || !data.length || !series.length) {
    return <div className="chart-empty">{t.analytic.noCharts}</div>;
  }

  const height = chart.type === "horizontal_bar" ? Math.max(260, data.length * 34) : 300;
  const tooltip = <Tooltip formatter={(value, name, props) => tooltipFormatter(value, name, props, chart.unit)} />;

  if (chart.type === "line") {
    return (
      <div className="report-chart" style={{ height }}>
        <div className="report-chart-title">{chart.title}</div>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 22, left: 12, bottom: 28 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(138,151,173,0.25)" />
            <XAxis dataKey={chart.x_field} tick={{ fill: "var(--muted)", fontSize: 11 }} />
            <YAxis tickFormatter={(v) => fmtValue(v, chart.unit)} tick={{ fill: "var(--muted)", fontSize: 11 }} width={86} />
            {tooltip}
            <Legend />
            {series.map((s, i) => (
              <Line key={s.value_field} type="monotone" dataKey={s.value_field} name={s.name} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (chart.type === "horizontal_bar") {
    return (
      <div className="report-chart" style={{ height }}>
        <div className="report-chart-title">{chart.title}</div>
        <ResponsiveContainer width="100%" height="100%">
          <RBarChart data={data} layout="vertical" margin={{ top: 12, right: 26, left: 110, bottom: 18 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(138,151,173,0.25)" />
            <XAxis type="number" tickFormatter={(v) => fmtValue(v, chart.unit)} tick={{ fill: "var(--muted)", fontSize: 11 }} />
            <YAxis type="category" dataKey={chart.x_field} tick={{ fill: "var(--muted)", fontSize: 11 }} width={105} />
            {tooltip}
            <Legend />
            <Bar dataKey={series[0].value_field} name={series[0].name} fill={COLORS[0]} radius={[0, 4, 4, 0]} />
          </RBarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const stacked = chart.type === "stacked_bar";
  return (
    <div className="report-chart" style={{ height }}>
      <div className="report-chart-title">{chart.title}</div>
      <ResponsiveContainer width="100%" height="100%">
        <RBarChart data={data} margin={{ top: 12, right: 22, left: 12, bottom: 28 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(138,151,173,0.25)" />
          <XAxis dataKey={chart.x_field} tick={{ fill: "var(--muted)", fontSize: 11 }} />
          <YAxis tickFormatter={(v) => fmtValue(v, chart.unit)} tick={{ fill: "var(--muted)", fontSize: 11 }} width={86} />
          {tooltip}
          <Legend />
          {series.map((s, i) => (
            <Bar
              key={s.value_field}
              dataKey={s.value_field}
              name={s.name}
              fill={COLORS[i % COLORS.length]}
              stackId={stacked ? "total" : undefined}
              radius={stacked ? undefined : [4, 4, 0, 0]}
            />
          ))}
        </RBarChart>
      </ResponsiveContainer>
    </div>
  );
}
