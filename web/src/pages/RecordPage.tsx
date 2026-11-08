import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get, post } from "../api/client";
import type { Deal, RecordRevisionFull, RecordValue, Revision } from "../api/types";

// The deal record: every revision stays; uploading a file or saving edits makes the next one.

const CURRENCY: Record<string, string> = { GBP: "£", EUR: "€", USD: "$" };

function show(v: RecordValue): string {
  if (v.value === null || v.value === undefined || v.value === "") return "—";
  if (v.kind !== "number" || typeof v.value !== "number") return String(v.value);
  const n = v.value;
  if (v.value_kind === "percent") return `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (v.value_kind === "multiple") return `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}x`;
  // amounts and counts get thousands separators; a plain number (a year, an identifier) is shown as written
  if (v.value_kind !== "money" && v.value_kind !== "count") return String(n);
  const symbol = v.value_kind === "money" && v.unit ? (CURRENCY[v.unit] ?? `${v.unit} `) : "";
  return `${n < 0 ? "-" : ""}${symbol}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

/** A family's name: what its values' labels share ("Live subscription ARR, FY2023 year end" and
 *  "…, FY2024 year end" → "Live subscription ARR"), else the record's own key. */
function familyName(family: string, rows: RecordValue[]): string {
  const labels = rows.map((r) => r.label ?? "").filter(Boolean);
  if (rows.length === 1) return labels[0] ?? family.split(":").slice(1).join(":");
  let common = labels[0] ?? "";
  for (const l of labels.slice(1)) while (common && !l.startsWith(common)) common = common.slice(0, -1);
  common = common.replace(/[\s,;:(–-]+[^\s,;:(–-]*$/, "").trim();
  return common.length >= 3 ? common : family.split(":").slice(1).join(":").replace(/_/g, " ");
}

type Tab = "figures" | "tables" | "series" | "narrative" | "company";
const TABS: { key: Tab; label: string }[] = [
  { key: "figures", label: "Figures" },
  { key: "tables", label: "Tables" },
  { key: "series", label: "Series" },
  { key: "narrative", label: "Narrative" },
  { key: "company", label: "Company & angles" },
];

function tabOf(path: string): Tab {
  const kind = path.split(":")[0];
  if (kind === "field") return "figures";
  if (kind === "table" || kind === "angle_table") return "tables";
  if (kind === "series") return "series";
  if (kind === "narrative") return "narrative";
  return "company";
}

const TABLE_PATH = /^(table|angle_table):(.+)\[(.+)\]\.([^.\]]+)$/;

export function RecordPage() {
  const [deals, setDeals] = useState<Deal[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [record, setRecord] = useState<RecordRevisionFull | null>(null);
  const [tab, setTab] = useState<Tab>("figures");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadDeals = useCallback(async (select?: string) => {
    const d = await get<Deal[]>("/records");
    setDeals(d);
    setSelected(select ?? d[0]?.revisions[0]?.id ?? null);
  }, []);

  useEffect(() => {
    loadDeals().catch((e: Error) => setError(e.message));
  }, [loadDeals]);

  useEffect(() => {
    if (!selected) return;
    setRecord(null);
    get<RecordRevisionFull>(`/records/${selected}`).then(setRecord).catch((e: Error) => setError(e.message));
  }, [selected]);

  const latest = deals?.[0]?.revisions[0];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (record?.values ?? []).filter(
      (v) => tabOf(v.path) === tab && (!q || `${v.path} ${v.label ?? ""} ${v.value ?? ""}`.toLowerCase().includes(q)),
    );
  }, [record, tab, query]);
  const changedCount = record?.values.filter((v) => v.changed).length ?? 0;

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await post<Revision>("/records", form);
      await loadDeals(r.id);
      setMessage(`Uploaded as revision ${r.number}. Memos stay on the revision they were built from.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!record) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post<Revision>(`/records/${record.id}/revise`, { changes });
      setEditing(false);
      setChanges({});
      await loadDeals(r.id);
      setMessage(`Saved as revision ${r.number} (${Object.keys(changes).length} changed). Revision ${record.number} stays as it was.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = (v: RecordValue) => {
    if (!editing) {
      return (
        <span className={v.changed ? "changed" : undefined} title={v.changed ? `was ${v.before ?? "empty"}` : undefined}>
          {show(v)}
        </span>
      );
    }
    const current = changes[v.path] ?? (v.value === null ? "" : String(v.value));
    const long = v.kind === "text" && String(v.value ?? "").length > 60;
    const set = (text: string) =>
      setChanges((c) => {
        const next = { ...c };
        if (text === (v.value === null ? "" : String(v.value))) delete next[v.path];
        else next[v.path] = text;
        return next;
      });
    return long ? (
      <textarea className="cell-input" rows={3} value={current} onChange={(e) => set(e.target.value)} />
    ) : (
      <input
        className={`cell-input${v.path in changes ? " dirty" : ""}`}
        value={current}
        onChange={(e) => set(e.target.value)}
        title={v.value_kind === "percent" ? "as a fraction: 0.25 is 25%" : undefined}
      />
    );
  };

  return (
    <main className="page wide">
      <div className="page-head">
        <div>
          <h1>Deal record{record ? ` · ${record.deal}` : ""}</h1>
          <p className="muted">
            The figures every memo uses. Memos never change them; they change here, as a new revision.
          </p>
        </div>
        <div className="actions">
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
          <button onClick={() => fileRef.current?.click()} disabled={busy || editing}>
            Upload record
          </button>
          {editing ? (
            <>
              <button
                onClick={() => {
                  setEditing(false);
                  setChanges({});
                }}
                disabled={busy}
              >
                Cancel
              </button>
              <button className="primary" onClick={save} disabled={busy || Object.keys(changes).length === 0}>
                Save as revision {(latest?.number ?? 0) + 1}
                {Object.keys(changes).length > 0 ? ` (${Object.keys(changes).length})` : ""}
              </button>
            </>
          ) : (
            <button className="primary" onClick={() => setEditing(true)} disabled={!record || busy}>
              Edit values
            </button>
          )}
        </div>
      </div>
      {message && (
        <div className="notice" onClick={() => setMessage(null)}>
          {message}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {deals && deals.length === 0 && <div className="empty">No record yet. Upload the deal record (JSON).</div>}

      <div className="record">
        <aside className="revisions">
          {deals?.map((d) => (
            <div key={d.id}>
              <h3>{d.name}</h3>
              {d.revisions.map((r) => (
                <button
                  key={r.id}
                  className={`revision${r.id === selected ? " active" : ""}`}
                  onClick={() => !editing && setSelected(r.id)}
                >
                  <strong>Revision {r.number}</strong>
                  {r.id === d.revisions[0].id && <span className="badge">latest</span>}
                  <span className="muted small">
                    {new Date(r.created_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                    {" · "}
                    {r.created_by ?? "upload"}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </aside>
        <section className="values">
          {!record && selected && <p className="muted">Loading…</p>}
          {record && (
            <>
              <div className="values-head">
                <div className="tabs">
                  {TABS.map((t) => (
                    <button key={t.key} className={t.key === tab ? "active" : ""} onClick={() => setTab(t.key)}>
                      {t.label}
                      <span className="count">{record.values.filter((v) => tabOf(v.path) === t.key).length}</span>
                    </button>
                  ))}
                </div>
                <input className="search" placeholder="Search values" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              <p className="muted small">
                Revision {record.number} · as of {record.as_of ?? "—"} · {record.values.length} values
                {record.previous_id ? ` · ${changedCount} changed from the previous revision` : ""}
              </p>
              {tab === "tables" ? <Tables values={visible} field={field} /> : <Families values={visible} field={field} />}
            </>
          )}
        </section>
      </div>
    </main>
  );
}

/** Values grouped by family (a figure over its periods, a list's items). */
function Families({ values, field }: { values: RecordValue[]; field: (v: RecordValue) => JSX.Element }) {
  const groups = useMemo(() => {
    const out = new Map<string, RecordValue[]>();
    for (const v of values) {
      const key = v.family ?? v.path;
      out.set(key, [...(out.get(key) ?? []), v]);
    }
    return [...out.entries()];
  }, [values]);
  if (!groups.length) return <p className="muted">Nothing here.</p>;
  return (
    <div className="families">
      {groups.map(([family, rows]) => (
        <div key={family} className="family">
          <div className="family-name" title={family}>
            {familyName(family, rows)}
          </div>
          <table className="kv">
            <tbody>
              {rows.map((v) => (
                <tr key={v.path} className={v.forecast ? "forecast" : undefined}>
                  <td className="k" title={v.path}>
                    {rows.length === 1 ? v.source ?? "" : v.period ?? v.path.slice(family.length) ?? v.label}
                    {v.forecast && <span className="badge small">forecast</span>}
                  </td>
                  <td className="v">{field(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

/** Table cells rebuilt as their tables: rows by key, columns by name. */
function Tables({ values, field }: { values: RecordValue[]; field: (v: RecordValue) => JSX.Element }) {
  const tables = useMemo(() => {
    const out = new Map<string, { rows: string[]; cols: string[]; cells: Map<string, RecordValue> }>();
    for (const v of values) {
      const m = TABLE_PATH.exec(v.path);
      if (!m) continue;
      const [, , name, row, col] = m;
      const t = out.get(name) ?? { rows: [] as string[], cols: [] as string[], cells: new Map<string, RecordValue>() };
      if (!t.rows.includes(row)) t.rows.push(row);
      if (!t.cols.includes(col)) t.cols.push(col);
      t.cells.set(`${row}|${col}`, v);
      out.set(name, t);
    }
    return [...out.entries()];
  }, [values]);
  if (!tables.length) return <p className="muted">Nothing here.</p>;
  return (
    <div className="families">
      {tables.map(([name, t]) => (
        <div key={name} className="family">
          <div className="family-name">{name.replace(/\./g, " · ")}</div>
          <div className="scroll-x">
            <table className="grid">
              <thead>
                <tr>
                  <th />
                  {t.cols.map((c) => (
                    <th key={c}>{c.replace(/_/g, " ")}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r) => (
                  <tr key={r}>
                    <th>{r}</th>
                    {t.cols.map((c) => {
                      const v = t.cells.get(`${r}|${c}`);
                      return <td key={c}>{v ? field(v) : ""}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
