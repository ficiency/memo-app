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

type Offer = { from: string; path: string; label: string | null; oldValue: number; newText: string; used: string[]; chosen: boolean };

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
  const [usage, setUsage] = useState<Record<string, string[]>>({});
  const [offers, setOffers] = useState<Offer[] | null>(null);

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
    // which values the firm's memos use (through the binding), to show which place feeds a memo
    get<Record<string, string[]>>(`/records/${selected}/usage`).then(setUsage).catch(() => setUsage({}));
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

  // Before saving: other places that held the same old value (the record often repeats a figure). Offered,
  // never changed on their own; the ones a memo uses come first and checked.
  const offersFor = (edits: Record<string, string>): Offer[] => {
    if (!record) return [];
    const byPath = new Map(record.values.map((v) => [v.path, v]));
    const out: Offer[] = [];
    for (const [path, text] of Object.entries(edits)) {
      const v = byPath.get(path);
      if (!v || v.kind !== "number" || typeof v.value !== "number" || Math.abs(v.value) < 10 || v.value_kind === "percent") continue;
      for (const o of record.values) {
        if (o.path === path || o.path in edits || o.kind !== "number" || o.value !== v.value) continue;
        if (o.value_kind && v.value_kind && o.value_kind !== v.value_kind) continue;
        out.push({ from: path, path: o.path, label: o.label, oldValue: v.value, newText: text, used: usage[o.path] ?? [], chosen: Boolean(usage[o.path]) });
      }
    }
    return out.sort((a, b) => Number(b.used.length > 0) - Number(a.used.length > 0));
  };

  const save = async (extra: Record<string, string> = {}, asked = false) => {
    if (!record) return;
    if (!asked) {
      const found = offersFor(changes);
      if (found.length) {
        setOffers(found);
        return;
      }
    }
    setOffers(null);
    setBusy(true);
    setError(null);
    try {
      const all = { ...changes, ...extra };
      const r = await post<Revision>(`/records/${record.id}/revise`, { changes: all });
      setEditing(false);
      setChanges({});
      await loadDeals(r.id);
      setMessage(`Saved as revision ${r.number} (${Object.keys(all).length} changed). Revision ${record.number} stays as it was.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = (v: RecordValue) => {
    if (!editing) {
      return (
        <>
          <span className={v.changed ? "changed" : undefined} title={v.changed ? `was ${v.before ?? "empty"}` : undefined}>
            {show(v)}
          </span>
          {usage[v.path] && (
            <span className="used" title={`The firm's memos take ${usage[v.path].join(", ")} from here`}>
              memo: {usage[v.path].map((c) => c.replace(/_/g, " ")).join(", ")}
            </span>
          )}
        </>
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
              <button className="primary" onClick={() => save()} disabled={busy || Object.keys(changes).length === 0}>
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
      {offers && (
        <div className="overlay" onClick={() => setOffers(null)}>
          <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
            <h2>The same value is in other places</h2>
            <p className="muted">
              The record often repeats a figure. Change these too? The ones a memo uses are checked; the rest are probably the same
              number by chance.
            </p>
            <table className="list compact">
              <tbody>
                {offers.slice(0, 12).map((o, i) => (
                  <tr key={o.path}>
                    <td style={{ width: 30 }}>
                      <input
                        type="checkbox"
                        checked={o.chosen}
                        onChange={(e) => setOffers(offers.map((x, j) => (j === i ? { ...x, chosen: e.target.checked } : x)))}
                      />
                    </td>
                    <td>
                      {o.label ?? o.path}
                      <div className="mono small muted">{o.path}</div>
                    </td>
                    <td className="num">
                      {o.oldValue.toLocaleString()} → {o.newText}
                    </td>
                    <td>{o.used.length > 0 && <span className="used">memo: {o.used.map((c) => c.replace(/_/g, " ")).join(", ")}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {offers.length > 12 && <p className="muted small">{offers.length - 12} more places hold the same number; left as they are.</p>}
            <div className="dialog-actions">
              <button onClick={() => setOffers(null)}>Back</button>
              <button onClick={() => save({}, true)}>Save only my change</button>
              <button
                className="primary"
                onClick={() => save(Object.fromEntries(offers.filter((o) => o.chosen).map((o) => [o.path, o.newText])), true)}
              >
                Save with the checked ones
              </button>
            </div>
          </div>
        </div>
      )}

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
