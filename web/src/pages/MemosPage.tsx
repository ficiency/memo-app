import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import type { Deal, MemoSummary, Spec } from "../api/types";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function MemosPage() {
  const [memos, setMemos] = useState<MemoSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    get<MemoSummary[]>("/memos").then(setMemos).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Memos</h1>
          <p className="muted">Every memo is built from the firm's spec and a revision of the deal record.</p>
        </div>
        <button className="primary" onClick={() => setCreating(true)}>
          New memo
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!memos && !error && <p className="muted">Loading…</p>}
      {memos && memos.length === 0 && <div className="empty">No memos yet. Create the first one from the deal record.</div>}
      {memos && memos.length > 0 && (
        <table className="list">
          <thead>
            <tr>
              <th>Memo</th>
              <th>Stage</th>
              <th>Spec</th>
              <th>Record</th>
              <th>Created</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {memos.map((m) => (
              <tr key={m.id}>
                <td>
                  <Link to={`/memos/${m.id}`} className="strong-link">
                    {m.deal}
                  </Link>
                  <div className="muted small">{m.title}</div>
                </td>
                <td>{m.stage}</td>
                <td>
                  {m.spec} <span className="muted">v{m.spec_version}</span>
                </td>
                <td>rev {m.record_revision}</td>
                <td>
                  {when(m.created_at)}
                  {m.created_by && <div className="muted small">{m.created_by}</div>}
                </td>
                <td>
                  <span className={`badge ${m.status}`}>{m.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {creating && <NewMemoDialog onClose={() => setCreating(false)} />}
    </main>
  );
}

function NewMemoDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [versionId, setVersionId] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [stage, setStage] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [team, setTeam] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([get<Spec[]>("/specs"), get<Deal[]>("/records")])
      .then(([s, d]) => {
        setSpecs(s);
        setDeals(d);
        const v = s[0]?.versions[0];
        if (v) {
          setVersionId(v.id);
          setStage(v.stages[v.stages.length - 1] ?? "");
        }
        if (d[0]?.revisions[0]) setRevisionId(d[0].revisions[0].id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const version = useMemo(() => specs.flatMap((s) => s.versions).find((v) => v.id === versionId), [specs, versionId]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ memo_id: string }>("/memos", {
        spec_version_id: versionId,
        record_revision_id: revisionId,
        stage,
        memo_date: date,
        deal_team: team || null,
      });
      navigate(`/memos/${r.memo_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h2>New memo</h2>
        <p className="muted">The stage decides which sections are mandatory. Figures come from the record revision.</p>
        <label>
          Spec
          <select
            value={versionId}
            onChange={(e) => {
              setVersionId(e.target.value);
              const v = specs.flatMap((s) => s.versions).find((x) => x.id === e.target.value);
              setStage(v?.stages[v.stages.length - 1] ?? "");
            }}
          >
            {specs.map((s) =>
              s.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {s.name} · v{v.number} ({v.status})
                </option>
              )),
            )}
          </select>
        </label>
        <label>
          Stage
          <select value={stage} onChange={(e) => setStage(e.target.value)}>
            {version?.stages.map((st) => (
              <option key={st}>{st}</option>
            ))}
          </select>
        </label>
        <label>
          Deal record
          <select value={revisionId} onChange={(e) => setRevisionId(e.target.value)}>
            {deals.map((d) =>
              d.revisions.map((r) => (
                <option key={r.id} value={r.id}>
                  {d.name} · revision {r.number}
                  {r.as_of ? ` · as of ${r.as_of}` : ""}
                </option>
              )),
            )}
          </select>
        </label>
        <div className="row2">
          <label>
            Memo date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label>
            Deal team <span className="muted small">(one per line)</span>
            <textarea rows={2} value={team} onChange={(e) => setTeam(e.target.value)} />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="dialog-actions">
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="primary" onClick={create} disabled={busy || !versionId || !revisionId || !stage}>
            {busy ? "Building the memo…" : "Create memo"}
          </button>
        </div>
      </div>
    </div>
  );
}
