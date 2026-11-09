import { useCallback, useEffect, useMemo, useState } from "react";
import { get, post } from "../api/client";
import type { RecordRevisionFull, RecordValue } from "../api/types";

// The firm's binding: for each concept of the spec, where its value comes from in the deal record. The model
// proposed it and code checked that each place exists; a person confirms it or changes it here. Every change
// is kept, and memos built before it are offered the new figures ("See changes" on the memo).

type Flag = { kind: string; text: string; info?: boolean; places?: { path: string; label: string | null; value?: number | null }[] };
type Concept = {
  concept: string;
  description: string | null;
  concept_kind: string;
  value_kind: string | null;
  used_in: string[];
  kind: "path" | "derived" | "missing";
  path: string | null;
  formula: string | null;
  status: "found" | "list" | "derived" | "missing" | "broken";
  value: string | number | null;
  label: string | null;
  unit: string | null;
  period: string | null;
  flags: Flag[];
  approved_by: string | null;
  approved_at: string | null;
};
type Review = {
  spec_version: number;
  revision: { id: string; number: number };
  uploaded_revision: number;
  record_schema: string;
  concepts: Concept[];
  counts: Record<string, number>;
  to_look_at: number;
  approved: number;
  history: { concept: string; before: string | null; after: string | null; by: string | null; at: string }[];
};

const human = (s: string) => s.replace(/_/g, " ");
const show = (v: string | number | null) =>
  v === null || v === undefined ? "—" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : String(v);
const toLookAt = (c: Concept) => c.flags.some((f) => !f.info);

export function BindingPanel() {
  const [data, setData] = useState<Review | null>(null);
  const [filter, setFilter] = useState<"look" | "all">("look");
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [values, setValues] = useState<RecordValue[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await get<Review>("/bindings");
      setData(r);
      if (!values.length) setValues((await get<RecordRevisionFull>(`/records/${r.revision.id}`)).values);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [values.length]);

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Reading the binding against the record…</p>;
  const rows = data.concepts.filter((c) => filter === "all" || toLookAt(c));

  const confirm = async (concepts: string[]) => {
    await post("/bindings/approve", { concepts });
    load();
  };

  return (
    <section className="step-panel">
      <div className="step-head">
        <div>
          <h2>Where each figure comes from</h2>
          <p className="muted">
            The binding links each figure of the spec to a place in the deal record. The model proposed it and code checked that every
            place exists; only a person can tell it is the right datum. Changes are kept, and memos built before a change are offered
            the new figures.
          </p>
          <p className="muted small">
            Spec v{data.spec_version} · record revision {data.revision.number} (last upload: revision {data.uploaded_revision}) ·{" "}
            {data.counts.found + data.counts.list} found, {data.counts.derived} from a formula, {data.counts.missing} missing
            {data.counts.broken ? `, ${data.counts.broken} broken` : ""} · {data.approved} confirmed by a person
          </p>
        </div>
        <div className="actions">
          <button
            onClick={() => confirm(rows.filter((c) => c.kind !== "missing" && !c.approved_at && !toLookAt(c)).map((c) => c.concept))}
            title="Confirm the ones shown that need no look"
          >
            Confirm the plain ones
          </button>
        </div>
      </div>
      <div className="tabs" style={{ margin: "10px 0" }}>
        <button className={filter === "look" ? "active" : ""} onClick={() => setFilter("look")}>
          To look at <span className="count warn">{data.to_look_at}</span>
        </button>
        <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
          All <span className="count">{data.concepts.length}</span>
        </button>
      </div>
      <table className="list compact bindings">
        <thead>
          <tr>
            <th>Figure</th>
            <th>Comes from</th>
            <th>Value now</th>
            <th>Worth a look</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <BindingRow
              key={c.concept}
              c={c}
              open={editing === c.concept}
              onOpen={() => setEditing(editing === c.concept ? null : c.concept)}
              onConfirm={() => confirm([c.concept])}
              onSaved={() => {
                setEditing(null);
                load();
              }}
              values={values}
            />
          ))}
        </tbody>
      </table>
      {data.history.length > 0 && (
        <div className="block">
          <h3>Changes made by people</h3>
          <ul className="history">
            {data.history.map((h, i) => (
              <li key={i}>
                <strong>{human(h.concept)}</strong>: <span className="mono">{h.before ?? "none"}</span> →{" "}
                <span className="mono">{h.after ?? "none"}</span>{" "}
                <span className="muted small">
                  {h.by ?? ""} · {new Date(h.at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function BindingRow({
  c,
  open,
  onOpen,
  onConfirm,
  onSaved,
  values,
}: {
  c: Concept;
  open: boolean;
  onOpen: () => void;
  onConfirm: () => void;
  onSaved: () => void;
  values: RecordValue[];
}) {
  return (
    <>
      <tr className={toLookAt(c) ? "look" : undefined}>
        <td>
          <strong>{human(c.concept)}</strong>
          <div className="muted small">
            {c.concept_kind}
            {c.value_kind ? ` · ${c.value_kind}` : ""}
          </div>
          {c.used_in.length > 0 && (
            <div className="muted small" title={c.used_in.join("\n")}>
              used in {c.used_in[0]}
              {c.used_in.length > 1 ? ` +${c.used_in.length - 1}` : ""}
            </div>
          )}
        </td>
        <td>
          {c.kind === "missing" ? (
            <span className="muted">nowhere: the memo shows a pending</span>
          ) : (
            <>
              {c.label && <div>{c.label}</div>}
              <div className="mono small">{c.kind === "derived" ? `= ${c.formula}` : c.path}</div>
            </>
          )}
        </td>
        <td className="num">
          {show(c.value)}
          {c.unit && <span className="muted small"> {c.unit}</span>}
          {c.period && <div className="muted small">{c.period}</div>}
        </td>
        <td>
          {c.flags.map((f, i) => (
            <div key={i} className={`flag ${f.info ? "info" : f.kind}`}>
              {f.info ? "ⓘ" : "⚠"} {f.text}
              {f.places && f.places.length > 0 && (
                <div className="places">
                  {f.places.map((p, j) => (
                    <div key={j} className="mono small">
                      {p.label ? `${p.label} · ` : ""}
                      {p.path}
                      {p.value !== undefined && p.value !== null ? ` = ${show(p.value)}` : ""}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </td>
        <td className="row-actions">
          <button onClick={onOpen}>{open ? "Close" : "Change"}</button>
          {c.approved_at ? (
            <span className="ok small" title={`${c.approved_by} · ${new Date(c.approved_at).toLocaleString()}`}>
              ✓ confirmed
            </span>
          ) : (
            c.kind !== "missing" && <button onClick={onConfirm}>Confirm</button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="editing-row">
          <td colSpan={5}>
            <ChangeBinding c={c} values={values} onSaved={onSaved} />
          </td>
        </tr>
      )}
    </>
  );
}

function ChangeBinding({ c, values, onSaved }: { c: Concept; values: RecordValue[]; onSaved: () => void }) {
  const [kind, setKind] = useState<"path" | "derived" | "missing">(c.kind === "missing" ? "path" : c.kind);
  const [query, setQuery] = useState("");
  const [path, setPath] = useState(c.path ?? "");
  const [formula, setFormula] = useState(c.formula ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const matches = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return values
      .filter((v) => words.every((w) => `${v.path} ${v.label ?? ""} ${v.value ?? ""}`.toLowerCase().includes(w)))
      .slice(0, 25);
  }, [query, values]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await post(`/bindings/concepts/${encodeURIComponent(c.concept)}`, {
        kind,
        path: kind === "path" ? path : null,
        formula: kind === "derived" ? formula : null,
      });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="change-binding">
      <div className="tabs">
        <button className={kind === "path" ? "active" : ""} onClick={() => setKind("path")}>
          A value of the record
        </button>
        <button className={kind === "derived" ? "active" : ""} onClick={() => setKind("derived")}>
          A formula
        </button>
        <button className={kind === "missing" ? "active" : ""} onClick={() => setKind("missing")}>
          Not in the record
        </button>
      </div>
      {kind === "path" && (
        <>
          <input placeholder="Search the record: words of its label, its place or its value" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
          {matches.length > 0 && (
            <div className="matches">
              {matches.map((v) => (
                <button key={v.path} className={`match${v.path === path ? " chosen" : ""}`} onClick={() => setPath(v.path)}>
                  <span>{v.label ?? v.path}</span>
                  <span className="mono small">{v.path}</span>
                  <span className="num">{show(v.value)}</span>
                </button>
              ))}
            </div>
          )}
          <p className="small">
            Chosen: <span className="mono">{path || "—"}</span>
            <span className="muted"> (a place such as field:arr@last_actual moves on with each new record)</span>
          </p>
          <input className="mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="or type the place" />
        </>
      )}
      {kind === "derived" && (
        <input className="mono" value={formula} onChange={(e) => setFormula(e.target.value)} placeholder="e.g. field:arr@last_actual / field:sites@last_actual" />
      )}
      {kind === "missing" && <p className="muted">The memo shows this figure as a pending until the record has it.</p>}
      {error && <p className="error">{error}</p>}
      <div className="dialog-actions">
        <button className="primary" onClick={save} disabled={busy || (kind === "path" && !path) || (kind === "derived" && !formula)}>
          {busy ? "Checking…" : "Save and confirm"}
        </button>
      </div>
    </div>
  );
}
