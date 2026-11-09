import { useEffect, useMemo, useState } from "react";
import { get } from "../api/client";
import type { El } from "../api/types";

// A new table or chart for a slide the firm leaves free, from the deal record: a table is one of the record's
// tables (its rows and columns, or some of them); a chart is one or more of its figures with periods. Every
// value stays bound to the record; the server builds it in the firm's formats.

type RecordBoxes = { tables: Record<string, { rows: string[]; columns: string[] }>; families: Record<string, string> };
type Zone = number[];

let cache: Promise<RecordBoxes> | null = null;
let cachedFor = "";

function useRecordBoxes(memoId: string) {
  const [data, setData] = useState<RecordBoxes | null>(null);
  useEffect(() => {
    if (!cache || cachedFor !== memoId) {
      cache = get<RecordBoxes>(`/memos/${memoId}/record-boxes`);
      cachedFor = memoId;
    }
    cache.then(setData).catch(() => setData({ tables: {}, families: {} }));
  }, [memoId]);
  return data;
}

const CELL = /^(.+)\[(.+)\]\.([^.\]]+)$/;

/** What a person's table shows: the record table, its rows and columns, from the figures in its cells. */
export function tableChoice(el: El): { table: string; rows: string[]; columns: string[] } | null {
  let table = "";
  const rows: string[] = [];
  const columns: string[] = [];
  for (const c of el.table?.cells ?? [])
    for (const p of c.paragraphs)
      for (const x of p.segments) {
        const m = CELL.exec(x.path ?? "");
        if (!m) continue;
        table = m[1];
        if (!rows.includes(m[2])) rows.push(m[2]);
        if (!columns.includes(m[3])) columns.push(m[3]);
      }
  return table ? { table, rows, columns } : null;
}

/** What a person's chart shows: its figures, kind, periods and series names. */
export function chartChoice(el: El): { series: string[]; type: string; periods: number; names: Record<string, string> } | null {
  const ch = el.chart;
  if (!ch) return null;
  const series = ch.series.map((x) => x.path ?? "").filter(Boolean);
  const names = Object.fromEntries(ch.series.filter((x) => x.path && x.name).map((x) => [x.path as string, x.name as string]));
  return { series, type: ch.type, periods: Math.max(2, ...ch.series.map((x) => x.points.length)), names };
}

const pretty = (name: string) => name.replace(/^(angle_)?table:/, "").replace(/\./g, " · ").replace(/_/g, " ");

export function AddTable({
  memoId,
  zone,
  initial,
  onAdd,
  onClose,
}: {
  memoId: string;
  zone: Zone;
  initial?: { table: string; rows: string[]; columns: string[] } | null; // a box being changed
  onAdd: (payload: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const data = useRecordBoxes(memoId);
  const [query, setQuery] = useState("");
  const [table, setTable] = useState<string | null>(initial?.table ?? null);
  const [rows, setRows] = useState<string[]>(initial?.rows ?? []);
  const [columns, setColumns] = useState<string[]>(initial?.columns ?? []);
  const names = useMemo(
    () => Object.keys(data?.tables ?? {}).filter((n) => !query || pretty(n).toLowerCase().includes(query.toLowerCase())),
    [data, query],
  );
  const pick = (n: string) => {
    setTable(n);
    setRows(data!.tables[n].rows.slice(0, 8));
    setColumns(data!.tables[n].columns.slice(0, 6));
  };
  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const [zx, zy, zw, zh] = zone;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
        <h2>{initial ? "Change the table" : "Add a table from the deal record"}</h2>
        <p className="muted">Its cells are the record's values, written as the firm writes them, and stay bound to the record.</p>
        {!data && <p className="muted">Reading the record…</p>}
        {data && !table && (
          <>
            <input placeholder="Search the record's tables" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
            <div className="matches">
              {names.map((n) => (
                <button key={n} className="match two" onClick={() => pick(n)}>
                  <span>{pretty(n)}</span>
                  <span className="muted small">
                    {data.tables[n].rows.length} rows · {data.tables[n].columns.length} columns
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {data && table && data.tables[table] && (
          <>
            <p>
              <strong>{pretty(table)}</strong>{" "}
              <button className="link" onClick={() => setTable(null)}>
                (another table)
              </button>
            </p>
            <div className="pick-grid">
              <div>
                <h3>Rows</h3>
                {data.tables[table].rows.map((r) => (
                  <label key={r} className="check">
                    <input type="checkbox" checked={rows.includes(r)} onChange={() => toggle(rows, setRows, r)} /> {r}
                  </label>
                ))}
              </div>
              <div>
                <h3>Columns</h3>
                {data.tables[table].columns.map((c) => (
                  <label key={c} className="check">
                    <input type="checkbox" checked={columns.includes(c)} onChange={() => toggle(columns, setColumns, c)} /> {c.replace(/_/g, " ")}
                  </label>
                ))}
              </div>
            </div>
          </>
        )}
        <div className="dialog-actions">
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!table || !rows.length || !columns.length}
            onClick={() =>
              onAdd({
                type: "table",
                table,
                rows,
                columns,
                x_in: zx,
                y_in: zy,
                w_in: Math.min(zw, 1.4 * (columns.length + 1)),
                h_in: Math.min(zh, 0.32 * (rows.length + 1)),
              })
            }
          >
            {initial ? "Save" : "Add the table"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AddChart({
  memoId,
  zone,
  initial,
  onAdd,
  onClose,
}: {
  memoId: string;
  zone: Zone;
  initial?: { series: string[]; type: string; periods: number; names: Record<string, string> } | null; // a box being changed
  onAdd: (payload: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const data = useRecordBoxes(memoId);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<string[]>(initial?.series ?? []);
  const [type, setType] = useState(initial?.type ?? "column");
  const [periods, setPeriods] = useState(initial?.periods ?? 6);
  const [names, setNames] = useState<Record<string, string>>(initial?.names ?? {});
  const families = useMemo(
    () =>
      Object.entries(data?.families ?? {}).filter(
        ([f, label]) => !query || `${f} ${label}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [data, query],
  );
  const [zx, zy, zw, zh] = zone;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
        <h2>{initial ? "Change the chart" : "Add a chart from the deal record"}</h2>
        <p className="muted">One series per figure, over its last periods; forecasts are marked. It stays bound to the record.</p>
        {!data && <p className="muted">Reading the record…</p>}
        {data && (
          <>
            <input placeholder="Search the record's figures (ARR, revenue…)" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
            <div className="matches">
              {families.map(([f, label]) => (
                <label key={f} className={`match two${chosen.includes(f) ? " chosen" : ""}`}>
                  <span>
                    <input
                      type="checkbox"
                      checked={chosen.includes(f)}
                      onChange={() => setChosen((c) => (c.includes(f) ? c.filter((x) => x !== f) : [...c, f].slice(0, 4)))}
                    />{" "}
                    {label}
                  </span>
                  <span className="mono small">{f}</span>
                </label>
              ))}
            </div>
            {chosen.length > 0 && (
              <div className="series-names">
                <h3>Series names</h3>
                {chosen.map((f) => (
                  <label key={f}>
                    <span className="mono small">{f}</span>
                    <input
                      value={names[f] ?? data.families[f] ?? ""}
                      onChange={(e) => setNames((n) => ({ ...n, [f]: e.target.value }))}
                    />
                  </label>
                ))}
              </div>
            )}
            <div className="row2">
              <label>
                Kind
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="column">Columns</option>
                  <option value="bar">Bars</option>
                  <option value="line">Line</option>
                </select>
              </label>
              <label>
                Periods
                <input type="number" min={2} max={12} value={periods} onChange={(e) => setPeriods(Number(e.target.value))} />
              </label>
            </div>
          </>
        )}
        <div className="dialog-actions">
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!chosen.length}
            onClick={() =>
              onAdd({
                type: "chart",
                chart_type: type,
                series: chosen,
                periods,
                names: Object.fromEntries(chosen.filter((f) => names[f]?.trim()).map((f) => [f, names[f].trim()])),
                x_in: zx,
                y_in: zy,
                w_in: Math.min(zw, 6),
                h_in: Math.min(zh, 3.4),
              })
            }
          >
            {initial ? "Save" : "Add the chart"}
          </button>
        </div>
      </div>
    </div>
  );
}
