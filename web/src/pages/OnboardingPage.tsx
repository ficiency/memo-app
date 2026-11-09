import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { get, post } from "../api/client";
import type { El, SlideData, SpecView } from "../api/types";
import { Fit, Slide, useWidth, type Interaction } from "../canvas/Slide";
import { fontStack, Styler } from "../canvas/style";
import { BindingPanel } from "./BindingPanel";

const BINDING = 99; // the binding review, after the six steps

// Firm setup: the firm's materials go through six steps. Code extracts, the model interprets, code checks
// the model; a person reviews every step and approves it. The approved steps are written as the spec's next
// version, which memos are built from.

type StepInfo = {
  number: number;
  name: string;
  status: "pending" | "running" | "failed" | "review" | "approved";
  error: string | null;
  ran_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
  model_calls: number;
  cost_usd: number;
  unresolved: number;
  conflicts: number;
  questions: number;
};
type Overview = {
  firm: string;
  materials: { kind: string; name: string; size: number | null }[];
  steps: StepInfo[];
  versions: { id: string; spec: string; number: number; status: string; created_from: string | null; created_at: string }[];
};
type Evidence = { source?: string; quote?: string | null; ref?: string | null } | null | undefined;
type StepResult = { number: number; name: string; result: Record<string, any>; review: string | null }; // eslint-disable-line @typescript-eslint/no-explicit-any
type Decision = {
  id: string;
  topic: string;
  chosen: string | null;
  decided_by: string | null;
  options: { id: string; source: string; value: string; evidence: string | null }[];
};
type LayoutsView = { spec: SpecView; master: El[]; layouts: (SlideData & { sections: string[]; source: string; blank: boolean })[] };

const WHAT: Record<number, string> = {
  0: "Code reads every material: text, tables and page images of the guide; layouts, shapes and styles of the template and past memos.",
  1: "The firm's look: palette, fonts, text roles, number and date formats, table and chart style, citations, footer.",
  2: "The memo's sections, their order and counts, and which are mandatory at each stage.",
  3: "Each section's layouts: the template's own, or drawn from the guide when there is no template.",
  4: "What goes in each box: fixed words, record figures, memo data, and what the model or a person writes.",
  5: "The firm's rules as checks the server runs on every edit.",
};

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}

function Ev({ e }: { e: Evidence }) {
  if (!e || (!e.quote && !e.ref)) return null;
  return (
    <span className="evidence" title={e.quote ?? undefined}>
      {e.source && <span className={`src ${e.source}`}>{e.source}</span>}
      {e.ref ?? (e.quote ? `“${e.quote.slice(0, 60)}${e.quote.length > 60 ? "…" : ""}”` : "")}
    </span>
  );
}

export function OnboardingPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [selected, setSelected] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);

  const load = useCallback(() => get<Overview>("/onboarding").then(setData).catch((e: Error) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  // while a step runs, check on it every few seconds
  const running = data?.steps.some((s) => s.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [running, load]);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!data) return <main className="page"><p className="muted">Loading…</p></main>;

  const step = data.steps.find((s) => s.number === selected) ?? data.steps[0];
  const ready = data.steps.filter((s) => s.number > 0).every((s) => s.status === "approved");
  const latest = data.versions[0];
  const total = data.steps.reduce((sum, s) => sum + s.cost_usd, 0);

  const writeSpec = async () => {
    setWriting(true);
    try {
      const r = await post<{ spec: string; version: number }>("/onboarding/write");
      setNotice(
        `Written as ${r.spec} v${r.version}. New memos can be built from it; memos already made stay on their version. ` +
          "Figures that have no place in the record yet are being bound now: review them under Binding before the first memo.",
      );
      load();
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setWriting(false);
    }
  };

  return (
    <main className="page wide">
      <div className="page-head">
        <div>
          <h1>Firm setup</h1>
          <p className="muted">
            Code extracts, the model interprets, code checks the model, and a person approves each step.
            {latest && ` Current spec: ${latest.spec} v${latest.number}.`} Model cost of these results: ${total.toFixed(2)}.
          </p>
        </div>
        <button className="primary" disabled={!ready || writing} onClick={writeSpec} title={ready ? "" : "Approve steps 1 to 5 first"}>
          {writing ? "Writing…" : "Write spec version"}
        </button>
      </div>
      {notice && (
        <div className="notice" onClick={() => setNotice(null)}>
          {notice}
        </div>
      )}
      <Materials materials={data.materials} onAdded={load} />
      <div className="onb">
        <nav className="steps">
          {data.steps.map((s) => (
            <button key={s.number} className={`step${s.number === selected ? " active" : ""}`} onClick={() => setSelected(s.number)}>
              <span className={`step-dot ${s.status}`}>{s.status === "approved" ? "✓" : s.number}</span>
              <span className="step-text">
                <strong>{s.name}</strong>
                <span className="muted small">
                  {s.status === "approved" ? `approved${s.approved_by ? ` by ${s.approved_by.split("@")[0]}` : ""}` : s.status}
                  {s.conflicts + s.questions > 0 && ` · ${s.conflicts + s.questions} to decide`}
                </span>
              </span>
            </button>
          ))}
          <button className={`step${selected === BINDING ? " active" : ""}`} onClick={() => setSelected(BINDING)}>
            <span className="step-dot review">↔</span>
            <span className="step-text">
              <strong>Binding</strong>
              <span className="muted small">where each figure comes from</span>
            </span>
          </button>
        </nav>
        {selected === BINDING ? (
          <BindingPanel />
        ) : (
          <StepPanel key={selected} step={step} versionId={latest?.id ?? null} onChange={load} />
        )}
      </div>
    </main>
  );
}

function Materials({ materials, onAdded }: { materials: Overview["materials"]; onAdded: () => void }) {
  const [kind, setKind] = useState("memo");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const upload = async (file: File) => {
    setBusy(true);
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    try {
      await post("/onboarding/materials", form);
      onAdded();
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="materials">
      <div className="materials-list">
        {materials.map((m) => (
          <span key={m.name} className="material" title={m.name}>
            <span className={`kind ${m.kind}`}>{m.kind}</span>
            {m.name}
          </span>
        ))}
        {!materials.length && <span className="muted">No materials yet: at least one of a guide, a template or past memos.</span>}
      </div>
      <div className="actions">
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 120 }}>
          <option value="guide">Guide</option>
          <option value="template">Template</option>
          <option value="memo">Past memo</option>
          <option value="logo">Logo</option>
        </select>
        <input ref={ref} type="file" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <button onClick={() => ref.current?.click()} disabled={busy}>
          {busy ? "Uploading…" : "Add material"}
        </button>
      </div>
    </section>
  );
}

function StepPanel({ step, versionId, onChange }: { step: StepInfo; versionId: string | null; onChange: () => void }) {
  const [data, setData] = useState<StepResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRun, setConfirmRun] = useState(false);

  useEffect(() => {
    if (step.status === "pending" || step.status === "running") return;
    get<StepResult>(`/onboarding/steps/${step.number}`).then(setData).catch((e: Error) => setError(e.message));
  }, [step.number, step.status, step.ran_at]);

  const run = async () => {
    setConfirmRun(false);
    try {
      await post(`/onboarding/steps/${step.number}/run`);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const approve = async () => {
    try {
      await post(`/onboarding/steps/${step.number}/approve`);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="step-panel">
      <div className="step-head">
        <div>
          <h2>
            {step.number} · {step.name} <span className={`badge ${step.status}`}>{step.status}</span>
          </h2>
          <p className="muted">{WHAT[step.number]}</p>
          {step.ran_at && (
            <p className="muted small">
              Ran {when(step.ran_at)} · {step.model_calls} model call{step.model_calls === 1 ? "" : "s"} · ${step.cost_usd.toFixed(2)} ·{" "}
              {step.unresolved ? `${step.unresolved} checks still failing` : "every check passed"}
            </p>
          )}
        </div>
        <div className="actions">
          <button onClick={() => setConfirmRun(true)} disabled={step.status === "running"}>
            {step.status === "running" ? "Running…" : step.ran_at ? "Run again" : "Run step"}
          </button>
          <button className="primary" onClick={approve} disabled={!step.ran_at || step.status === "approved" || step.status === "running"}>
            {step.status === "approved" ? "Approved" : "Approve step"}
          </button>
        </div>
      </div>
      {step.error && <p className="error">The step stopped: {step.error}</p>}
      {error && <p className="error">{error}</p>}
      {step.status === "running" && <p className="muted">Running… the model is reading the materials; this takes a few minutes.</p>}
      {data && <StepBody number={step.number} result={data.result} review={data.review} versionId={versionId} />}
      {confirmRun && (
        <div className="overlay" onClick={() => setConfirmRun(false)}>
          <div className="dialog small-dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Run {step.name} again?</h2>
            <p>
              {step.number === 0
                ? "Code reads the materials again; no model call."
                : `This calls the model (last run: $${step.cost_usd.toFixed(2)}, ${step.model_calls} call${step.model_calls === 1 ? "" : "s"}). `}
              The approvals of this step and the ones after it are cleared, since they were made on the previous result.
            </p>
            <div className="dialog-actions">
              <button onClick={() => setConfirmRun(false)}>Cancel</button>
              <button className="primary" onClick={run}>
                Run
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepBody({ number, result, review, versionId }: { number: number; result: Record<string, any>; review: string | null; versionId: string | null }) {
  const answer = result.answer ?? result;
  return (
    <div className="step-body">
      {number === 0 && <StepMaterials result={result} />}
      {number === 1 && <StepStyle a={answer} />}
      {number === 2 && <StepStructure a={answer} />}
      {number === 3 && versionId && <StepLayouts versionId={versionId} conflicts={result.conflicts ?? []} />}
      {number === 4 && <StepRecipe a={answer} />}
      {number === 5 && <StepRules a={answer} />}
      {[1, 2].includes(number) && (answer.conflicts?.length ?? 0) > 0 && versionId && <Conflicts conflicts={answer.conflicts} versionId={versionId} />}
      {(result.unresolved?.length ?? 0) > 0 && (
        <Block title="Checks still failing">
          <ul>
            {result.unresolved.map((u: string, i: number) => (
              <li key={i}>{String(u)}</li>
            ))}
          </ul>
        </Block>
      )}
      {review && (
        <details className="raw">
          <summary>Full review text</summary>
          <pre>{review}</pre>
        </details>
      )}
    </div>
  );
}

function Block({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <div className="block">
      <h3>{title}</h3>
      {note && <p className="muted small">{note}</p>}
      {children}
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepMaterials({ result }: { result: Record<string, any> }) {
  return (
    <Block title="What was read">
      <table className="list compact">
        <tbody>
          {(result.materials ?? []).map((m: Record<string, unknown>, i: number) => (
            <tr key={i}>
              <td>
                <span className={`kind ${m.kind}`}>{String(m.kind)}</span>
              </td>
              <td>{String(m.file)}</td>
              <td className="muted">
                {m.pages ? `${m.pages} pages` : ""}
                {m.tables ? ` · ${m.tables} tables` : ""}
                {m.slides ? `${m.slides} slides` : ""}
                {m.layouts ? ` · ${m.layouts} layouts` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(result.missing ?? []).length > 0 && <p className="muted">Not given: {result.missing.join(", ")}. The steps work from what there is.</p>}
    </Block>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepStyle({ a }: { a: Record<string, any> }) {
  const palette: Record<string, string> = Object.fromEntries((a.palette ?? []).map((p: { name: string; hex: string }) => [p.name.toLowerCase(), p.hex]));
  const hex = (c: string | null | undefined) => (c ? (c.startsWith("#") ? c : palette[c.toLowerCase()]) : undefined);
  return (
    <>
      <Block title="Identity and page">
        <p>
          <strong>{a.identity?.memo_name?.value}</strong> <Ev e={a.identity?.memo_name?.evidence} />
        </p>
        <p className="muted">
          Stages: {(a.identity?.stages ?? []).map((s: { name: string }) => s.name).join(", ")} · Page {a.page?.width_in} × {a.page?.height_in} in ·
          Fonts: headings {a.fonts?.heading}, body {a.fonts?.body}, tables {a.fonts?.tables}
        </p>
      </Block>
      <Block title="Palette">
        <div className="swatches">
          {(a.palette ?? []).map((p: { name: string; hex: string; use?: string; evidence?: Evidence }) => (
            <div key={p.name} className="swatch">
              <span className="chipcolor" style={{ background: p.hex }} />
              <div>
                <strong>{p.name}</strong> <span className="muted small">{p.hex}</span>
                <div className="small">{p.use}</div>
                <Ev e={p.evidence} />
              </div>
            </div>
          ))}
        </div>
      </Block>
      <Block title="Text roles" note="Each role as it will look on a slide.">
        <table className="list compact">
          <tbody>
            {(a.text_roles ?? []).map((r: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
              <tr key={r.role}>
                <td className="muted small" style={{ width: 210 }}>{r.role}</td>
                <td>
                  <span
                    style={{
                      fontFamily: fontStack(r.font),
                      fontSize: Math.min(Math.max((r.size_pt ?? 12) * 1.1, 10), 34),
                      fontWeight: r.bold ? 700 : 400,
                      fontStyle: r.italic ? "italic" : "normal",
                      textTransform: r.caps ? "uppercase" : undefined,
                      color: hex(r.color) ?? "#222",
                    }}
                  >
                    {r.applies_to ?? r.role}
                  </span>
                </td>
                <td className="muted small">
                  {r.font} {r.size_pt}pt{r.min_size_pt ? ` (min ${r.min_size_pt})` : ""} · {r.color}
                </td>
                <td>
                  <Ev e={r.evidence} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>
      <Block title="Numbers and dates" note="Every format is checked by writing the firm's own examples back.">
        <table className="list compact">
          <tbody>
            {(a.formats ?? []).map((f: Record<string, any>, i: number) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
              <tr key={i}>
                <td style={{ width: 120 }}>
                  <strong>{f.item}</strong>
                  <div className="muted small">{f.context}</div>
                </td>
                <td>{f.rule}</td>
                <td className="mono">{(f.examples ?? []).join("  ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>
      <Block title="Tables and charts">
        <div className="kvgrid">
          {Object.entries(a.tables ?? {})
            .filter(([k, v]) => k !== "evidence" && v !== null && typeof v !== "object")
            .map(([k, v]) => (
              <div key={k}>
                <span className="muted small">{k.replace(/_/g, " ")}</span>
                <span>
                  {hex(String(v)) && <span className="dotcolor" style={{ background: hex(String(v)) }} />}
                  {String(v)}
                </span>
              </div>
            ))}
          {Object.entries(a.charts ?? {})
            .filter(([k, v]) => k !== "evidence" && v !== null && typeof v !== "object")
            .map(([k, v]) => (
              <div key={`c${k}`}>
                <span className="muted small">chart · {k.replace(/_/g, " ")}</span>
                <span>{String(v)}</span>
              </div>
            ))}
        </div>
      </Block>
    </>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepStructure({ a }: { a: Record<string, any> }) {
  const stages: { name: string; required: string[] }[] = a.stages ?? [];
  return (
    <>
      <Block title="Sections" note="In order. Locked sections do not move; mandatory ones are never deleted.">
        <table className="list compact">
          <thead>
            <tr>
              <th>Section</th>
              <th>Status</th>
              <th>Count</th>
              {stages.map((s) => (
                <th key={s.name}>{s.name}</th>
              ))}
              <th>Must contain</th>
            </tr>
          </thead>
          <tbody>
            {(a.sections ?? []).map((s: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
              <tr key={s.name}>
                <td>
                  <strong>{s.name}</strong>
                  <div className="muted small">{(s.layouts ?? []).join(", ")}</div>
                </td>
                <td>
                  <span className={`badge ${s.status}`}>{s.status}</span>
                </td>
                <td>{s.count}</td>
                {stages.map((st) => (
                  <td key={st.name} className="center-cell">
                    {st.required.includes(s.name) ? "●" : "○"}
                  </td>
                ))}
                <td className="small">{s.must_contain}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">● mandatory at that stage · ○ optional</p>
      </Block>
    </>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepRecipe({ a }: { a: Record<string, any> }) {
  const byLayout = useMemo(() => {
    const out = new Map<string, Record<string, any>[]>(); // eslint-disable-line @typescript-eslint/no-explicit-any
    for (const r of a.recipes ?? []) out.set(r.layout, [...(out.get(r.layout) ?? []), r]);
    return [...out.entries()];
  }, [a]);
  const label = (p: Record<string, any>) => { // eslint-disable-line @typescript-eslint/no-explicit-any
    const t = p.type;
    if (t === "fixed") return <span>{p.text}</span>;
    if (t === "figure" || t === "fact") return <span className="chip">{p.concept}</span>;
    if (t === "memo_data") return <span className="tag">{p.concept}</span>;
    if (t === "model_writes") return <span className="slot">‹model: {p.what ?? p.text}›</span>;
    if (t === "person_writes") return <span className="slot">‹person: {p.what ?? p.text}›</span>;
    if (t === "conditional") return <span className="slot">‹if {p.when}: {p.text ?? p.what}›</span>;
    return <span className="tag">{t}{p.concept ? `: ${p.concept}` : ""}</span>;
  };
  return (
    <>
      {(a.questions ?? []).length > 0 && (
        <Block title="Questions for the firm" note="The memos and the guide do not settle these; the answer is the firm's.">
          <ul>
            {a.questions.map((q: string, i: number) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </Block>
      )}
      {(a.accidents ?? []).length > 0 && (
        <Block title="Kept out (accidents in past memos)" note="Style comes from the theme, layouts or guide; from memos only content.">
          <ul>
            {a.accidents.map((q: string, i: number) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </Block>
      )}
      <Block title="What goes in each box" note="Grey: written by the model or a person. Underlined: a figure from the deal record.">
        {byLayout.map(([layout, recipes]) => (
          <div key={layout} className="recipe-layout">
            <h4>{layout}</h4>
            <table className="list compact">
              <tbody>
                {recipes.map((r) => (
                  <tr key={r.box_id}>
                    <td className="muted small" style={{ width: 70 }}>{r.box_id}</td>
                    <td>
                      {(r.pattern ?? []).map((p: Record<string, unknown>, i: number) => (
                        <span key={i}>{label(p)} </span>
                      ))}
                      {r.notes && <div className="muted small">{r.notes}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Block>
    </>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StepRules({ a }: { a: Record<string, any> }) {
  return (
    <>
      <Block title="Rules the server checks" note="Each rule is a type from the library with its parameters, so it runs as code.">
        <table className="list compact">
          <tbody>
            {(a.rules ?? []).map((r: Record<string, any>, i: number) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
              <tr key={i}>
                <td style={{ width: 170 }}>
                  <span className="tag">{r.type}</span>
                </td>
                <td>
                  {r.says}
                  <div>
                    <Ev e={r.evidence} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>
      {(a.unmapped ?? []).length > 0 && (
        <Block title="Rules with no check yet" note="Said by the firm, but the library has no type for them: shown to people, not enforced.">
          <ul>
            {a.unmapped.map((u: { says: string; why_no_type?: string }, i: number) => (
              <li key={i}>
                {u.says}
                {u.why_no_type && <div className="muted small">{u.why_no_type}</div>}
              </li>
            ))}
          </ul>
        </Block>
      )}
    </>
  );
}

type StepConflict = { topic: string; options: { source: string; value: string; evidence?: Evidence }[]; recommendation?: string };

function Conflicts({ conflicts, versionId }: { conflicts: StepConflict[]; versionId: string }) {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  useEffect(() => {
    get<Decision[]>(`/specs/versions/${versionId}/decisions`).then(setDecisions).catch(() => undefined);
  }, [versionId]);
  const choose = async (d: Decision, optionId: string) => {
    await post(`/specs/versions/${versionId}/decisions/${d.id}`, { option_id: optionId });
    setDecisions(await get<Decision[]>(`/specs/versions/${versionId}/decisions`));
  };
  return (
    <Block title="Where the materials disagree" note="No source wins by default: the evidence is shown and a person chooses. The choice is stored with who made it.">
      {conflicts.map((c, i) => {
        const d = decisions.find((x) => x.topic === c.topic);
        return (
          <div key={i} className="conflict-card">
            <strong>{c.topic}</strong>
            <div className="options">
              {c.options.map((o, j) => {
                const option = d?.options.find((x) => x.source === o.source && x.value === String(o.value));
                const chosen = Boolean(option && d?.chosen === option.id);
                return (
                  <button key={j} className={`option${chosen ? " chosen" : ""}`} disabled={!option} onClick={() => d && option && choose(d, option.id)}>
                    <span className={`src ${o.source}`}>{o.source}</span>
                    <span>{String(o.value)}</span>
                    <Ev e={o.evidence} />
                    {chosen && <span className="small ok">chosen{d?.decided_by ? ` by ${d.decided_by.split("@")[0]}` : ""}</span>}
                  </button>
                );
              })}
            </div>
            {c.recommendation && <p className="muted small">Model's reading: {c.recommendation}</p>}
          </div>
        );
      })}
    </Block>
  );
}

const NO_EDIT: Interaction = {
  me: "",
  holders: new Map(),
  colors: new Map(),
  drafts: {},
  pending: {},
  editing: null,
  onPick: () => undefined,
  onDraft: () => undefined,
  onLeave: () => undefined,
  onFigureRemoved: () => undefined,
};

function StepLayouts({ versionId, conflicts }: { versionId: string; conflicts: { role: string; what: string; template: string; guide: string; boxes: string[] }[] }) {
  const [view, setView] = useState<LayoutsView | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [ref, width] = useWidth<HTMLDivElement>();
  useEffect(() => {
    get<LayoutsView>(`/specs/versions/${versionId}/layouts`).then(setView).catch(() => undefined);
  }, [versionId]);
  const styler = useMemo(() => (view ? new Styler(view.spec) : null), [view]);
  if (!view || !styler) return <p className="muted">Painting the layouts…</p>;
  const thumb = Math.max(Math.floor((width - 2 * 16 - 3 * 18) / 3), 200); // two gaps, each card's padding and border
  return (
    <>
      <Block
        title="Layouts"
        note="Each layout painted by the same canvas memos use: fixed words, figures from the record (underlined) and what the model or a person writes (grey). Open one to check it closely."
      >
        <div className="layout-grid" ref={ref}>
          {width > 0 &&
            view.layouts.map((l, i) => (
              <button key={l.id} className="layout-card" onClick={() => setOpen(i)}>
                <Fit width={thumb} pageWidthIn={view.spec.page.w_in} pageHeightIn={view.spec.page.h_in}>
                  <Slide slide={l} master={view.master} styler={styler} ui={NO_EDIT} />
                </Fit>
                <span className="layout-name">{l.layout}</span>
                <span className="muted small">
                  {l.sections.join(", ") || "no section"} · {l.source}
                </span>
              </button>
            ))}
        </div>
      </Block>
      {conflicts.length > 0 && (
        <Block title="Template and guide disagree" note="The template's value is used; the guide's is shown for a person to judge.">
          <table className="list compact">
            <tbody>
              {conflicts.map((c, i) => (
                <tr key={i}>
                  <td>{c.role}</td>
                  <td>{c.what}</td>
                  <td>
                    template <strong>{c.template}</strong>
                  </td>
                  <td>
                    guide <strong>{c.guide}</strong>
                  </td>
                  <td className="muted small">{c.boxes.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Block>
      )}
      {open !== null && (
        <div className="overlay" onClick={() => setOpen(null)}>
          <div className="layout-open" onClick={(e) => e.stopPropagation()}>
            <div className="panel-head">
              <strong>{view.layouts[open].layout}</strong>
              <span className="actions">
                <button disabled={open === 0} onClick={() => setOpen(open - 1)}>
                  ←
                </button>
                <button disabled={open === view.layouts.length - 1} onClick={() => setOpen(open + 1)}>
                  →
                </button>
                <button onClick={() => setOpen(null)}>Close</button>
              </span>
            </div>
            <Fit width={Math.min(window.innerWidth - 120, 1200)} pageWidthIn={view.spec.page.w_in} pageHeightIn={view.spec.page.h_in}>
              <Slide slide={view.layouts[open]} master={view.master} styler={styler} ui={NO_EDIT} />
            </Fit>
          </div>
        </div>
      )}
    </>
  );
}
