import { useEffect, useMemo, useState } from "react";
import { get } from "../api/client";

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

const pretty = (name: string) => name.replace(/^(angle_)?table:/, "").replace(/\./g, " · ").replace(/_/g, " ");

export function AddTable({ memoId, zone, onAdd, onClose }: { memoId: string; zone: Zone; onAdd: (payload: Record<string, unknown>) => void; onClose: () => void }) {
  const data = useRecordBoxes(memoId);
  const [query, setQuery] = useState("");
  const [table, setTable] = useState<string | null>(null);
  const [rows, setRows] = useState<string[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
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
        <h2>Add a table from the deal record</h2>
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
        {data && table && (
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
            Add the table
          </button>
        </div>
      </div>
    </div>
  );
}

export function AddChart({ memoId, zone, onAdd, onClose }: { memoId: string; zone: Zone; onAdd: (payload: Record<string, unknown>) => void; onClose: () => void }) {
  const data = useRecordBoxes(memoId);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [type, setType] = useState("column");
  const [periods, setPeriods] = useState(6);
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
        <h2>Add a chart from the deal record</h2>
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
              onAdd({ type: "chart", chart_type: type, series: chosen, periods, x_in: zx, y_in: zy, w_in: Math.min(zw, 6), h_in: Math.min(zh, 3.4) })
            }
          >
            Add the chart
          </button>
        </div>
      </div>
    </div>
  );
}
