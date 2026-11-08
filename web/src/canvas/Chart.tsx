import type { ChartData } from "../api/types";
import { fontStack, ptPx, type Styler } from "./style";

// A chart is data: its series drawn as SVG here, as native charts in PowerPoint. Columns, bars (clustered or
// stacked) and lines; other kinds fall back to columns.

const FALLBACK = ["#4C6A92", "#C8A24A", "#8A5A83", "#5E8C6A", "#B26A00", "#6E6A66"];

function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e9) return `${+(n / 1e9).toFixed(1)}bn`;
  if (a >= 1e6) return `${+(n / 1e6).toFixed(1)}m`;
  if (a >= 1e3) return `${+(n / 1e3).toFixed(0)}k`;
  if (a > 0 && a < 1) return `${+(n * 100).toFixed(0)}%`;
  return `${+n.toFixed(1)}`;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((m) => m >= v) ?? v;
}

export function Chart({ chart, width, height, styler }: { chart: ChartData; width: number; height: number; styler: Styler }) {
  const series = chart.series;
  const categories = series[0]?.points.map((p) => p.category) ?? [];
  if (!series.length || !categories.length) {
    return <div className="chart-empty">No chart data</div>;
  }
  const type = chart.type;
  const stacked = type.startsWith("stacked");
  const horizontal = type.includes("bar");
  const line = type === "line" || type === "area";
  const colors = series.map(
    (s, i) => styler.color(s.color) ?? styler.color(styler.spec.chart_style.series_colors[i]) ?? FALLBACK[i % FALLBACK.length],
  );
  const textColor = styler.color(styler.tableRole()?.color_ref) ?? "#444";
  const font = fontStack(styler.tableRole()?.font ?? styler.spec.fonts.body);
  const fs = ptPx(styler.spec.chart_style.labels_on === false ? 9 : 9);
  const legend = series.length > 1;
  const top = 8;
  const bottom = (horizontal ? 18 : 22) + (legend ? 20 : 0);
  const left = horizontal ? 70 : 44;
  const right = 8;
  const W = Math.max(width - left - right, 10);
  const H = Math.max(height - top - bottom, 10);

  const value = (s: number, c: number) => series[s].points[c]?.value ?? 0;
  const totals = categories.map((_, c) =>
    stacked ? series.reduce((sum, _s, s) => sum + Math.max(value(s, c), 0), 0) : Math.max(...series.map((_s, s) => value(s, c))),
  );
  const max = niceMax(Math.max(...totals, 0));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const band = (horizontal ? H : W) / categories.length;
  const gap = band * 0.25;
  const per = stacked || line ? band - gap : (band - gap) / series.length;
  const scale = (v: number) => (Math.max(v, 0) / max) * (horizontal ? W : H);

  const marks: JSX.Element[] = [];
  categories.forEach((cat, c) => {
    let stack = 0;
    series.forEach((s, si) => {
      const v = value(si, c);
      const forecast = s.points[c]?.forecast;
      const len = scale(v);
      if (line) return;
      const offset = stacked ? 0 : per * si;
      const x = horizontal ? left + scale(stack) : left + c * band + gap / 2 + offset;
      const y = horizontal ? top + c * band + gap / 2 + offset : top + H - len - scale(stack);
      marks.push(
        <rect
          key={`${si}-${cat}`}
          x={x}
          y={y}
          width={horizontal ? len : per}
          height={horizontal ? per : len}
          fill={colors[si]}
          fillOpacity={forecast ? 0.55 : 1}
          stroke={forecast ? colors[si] : undefined}
          strokeDasharray={forecast ? "3 2" : undefined}
        >
          <title>{`${s.name ?? ""} ${cat}: ${v}`}</title>
        </rect>,
      );
      if (styler.spec.chart_style.labels_on && !stacked && v) {
        marks.push(
          <text
            key={`l${si}-${cat}`}
            x={horizontal ? x + len + 3 : x + per / 2}
            y={horizontal ? y + per / 2 + fs / 3 : y - 3}
            textAnchor={horizontal ? "start" : "middle"}
            fontSize={fs * 0.9}
            fill={textColor}
          >
            {compact(v)}
          </text>,
        );
      }
      if (stacked) stack += Math.max(v, 0);
    });
  });
  if (line) {
    series.forEach((_s, si) => {
      const pts = categories.map((_, c) => [left + c * band + band / 2, top + H - scale(value(si, c))]);
      marks.push(
        <polyline key={`p${si}`} points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={colors[si]} strokeWidth={2} />,
      );
      pts.forEach(([x, y], c) =>
        marks.push(<circle key={`c${si}-${c}`} cx={x} cy={y} r={2.5} fill={colors[si]} />),
      );
    });
  }

  return (
    <svg width={width} height={height} style={{ fontFamily: font, overflow: "visible" }}>
      {ticks.map((t) =>
        horizontal ? (
          <g key={t}>
            <line x1={left + scale(t)} x2={left + scale(t)} y1={top} y2={top + H} stroke="#e4e4e4" />
            <text x={left + scale(t)} y={top + H + 12} fontSize={fs} fill={textColor} textAnchor="middle">
              {compact(t)}
            </text>
          </g>
        ) : (
          <g key={t}>
            {styler.spec.chart_style.gridlines !== false && <line x1={left} x2={left + W} y1={top + H - scale(t)} y2={top + H - scale(t)} stroke="#e4e4e4" />}
            <text x={left - 4} y={top + H - scale(t) + fs / 3} fontSize={fs} fill={textColor} textAnchor="end">
              {compact(t)}
            </text>
          </g>
        ),
      )}
      {marks}
      {categories.map((cat, c) =>
        horizontal ? (
          <text key={cat} x={left - 4} y={top + c * band + band / 2 + fs / 3} fontSize={fs} fill={textColor} textAnchor="end">
            {cat}
          </text>
        ) : (
          <text key={cat} x={left + c * band + band / 2} y={top + H + 13} fontSize={fs} fill={textColor} textAnchor="middle">
            {cat}
          </text>
        ),
      )}
      <line x1={left} x2={horizontal ? left : left + W} y1={top + H} y2={horizontal ? top : top + H} stroke="#bdbdbd" />
      {legend &&
        series.map((s, si) => (
          <g key={`lg${si}`} transform={`translate(${left + si * Math.min(W / series.length, 160)}, ${height - 10})`}>
            <rect width={9} height={9} y={-8} fill={colors[si]} />
            <text x={13} fontSize={fs} fill={textColor}>
              {s.name ?? s.concept ?? `Series ${si + 1}`}
            </text>
          </g>
        ))}
    </svg>
  );
}
