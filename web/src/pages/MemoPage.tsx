import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post, withEmail } from "../api/client";
import type { Conflict, El, ImportResult, MemoView, Person, SlideData, UpdateChanges, UpdateInfo } from "../api/types";
import { useAuth } from "../auth";
import { Fit, Slide, useWidth, type Interaction } from "../canvas/Slide";
import { Styler } from "../canvas/style";
import { useLive, type LiveMessage } from "../canvas/useLive";

// The memo: its slides painted from the database, edited live with the others in it. One person per box;
// every edit goes through the server's door, and its answer updates everyone's screen.

const PEOPLE_COLORS = ["#2F6FDE", "#D9480F", "#2B8A3E", "#9C36B5", "#C2255C", "#1098AD"];

function colorFor(email: string): string {
  let h = 0;
  for (const c of email) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PEOPLE_COLORS[h % PEOPLE_COLORS.length];
}

type Toast = { id: number; text: string; tone?: "warn" | "ok" };
type Notice = { title: string; body: string };

function replaceElement(slides: SlideData[], el: El): SlideData[] {
  return slides.map((s) => {
    if (s.id !== el.slide_id) return s;
    const exists = s.elements.some((e) => e.id === el.id);
    const elements = el.deleted
      ? s.elements.filter((e) => e.id !== el.id)
      : exists
        ? s.elements.map((e) => (e.id === el.id ? el : e))
        : [...s.elements, el];
    return { ...s, elements };
  });
}

export function MemoPage() {
  const { memoId = "" } = useParams();
  const { me } = useAuth();
  const [view, setView] = useState<MemoView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, string>>({});
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const [editing, setEditing] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [panel, setPanel] = useState<"conflicts" | null>(null);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [importing, setImporting] = useState<ImportResult | "busy" | null>(null);
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [changes, setChanges] = useState<UpdateChanges | "loading" | null>(null);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const wantTake = useRef<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [mainRef, mainWidth] = useWidth<HTMLDivElement>();
  const email = me?.email ?? "";

  const toast = useCallback((text: string, tone?: Toast["tone"]) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const load = useCallback(async () => {
    try {
      const v = await get<MemoView>(`/memos/${memoId}`);
      setView(v);
      setPeople(v.people);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [memoId]);

  const loadConflicts = useCallback(() => {
    get<Conflict[]>(`/memos/${memoId}/conflicts`).then(setConflicts).catch(() => undefined);
  }, [memoId]);

  // a newer revision of the deal record than the one this memo was built from
  const loadUpdate = useCallback(() => {
    get<UpdateInfo>(`/memos/${memoId}/updates`).then(setUpdate).catch(() => undefined);
  }, [memoId]);

  useEffect(() => {
    load();
    loadConflicts();
    loadUpdate();
  }, [load, loadConflicts, loadUpdate]);

  const onMessage = useCallback(
    (m: LiveMessage) => {
      if (m.type === "people") {
        setPeople(m.people);
        const held = new Set(m.people.map((p) => p.editing).filter(Boolean));
        setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([k]) => held.has(k))));
        const mine = m.people.find((p) => p.email === email && p.editing === wantTake.current);
        if (wantTake.current && mine) {
          setEditing(wantTake.current);
          wantTake.current = null;
        }
      } else if (m.type === "box_taken") {
        if (wantTake.current === m.element_id) wantTake.current = null;
        toast(`${m.by.split("@")[0]} is editing this box`, "warn");
      } else if (m.type === "draft") {
        setDrafts((d) => ({ ...d, [m.element_id]: m.text }));
      } else if (m.type === "operation") {
        if (m.element) setView((v) => (v ? { ...v, slides: replaceElement(v.slides, m.element as El), memo: { ...v.memo, sequence: m.sequence } } : v));
        else if (m.slides) load();
        if (m.by === email) {
          setPending((p) => {
            const next = { ...p };
            delete next[m.target];
            return next;
          });
          if (m.status === "refused") {
            const figure = (m.reason ?? "").includes("deal record");
            setNotice({
              title: figure ? "Figures come from the deal record" : "This edit was not saved",
              body: figure
                ? `${m.reason}. Figures are never typed in a memo: change the value in the deal record and the memo picks it up.`
                : m.reason ?? "The firm's rules do not allow it.",
            });
          } else if (m.status === "kept_aside") {
            toast("Your edit was kept aside: the box changed since you opened it. See Conflicts.", "warn");
            loadConflicts();
          }
        } else if (m.status === "kept_aside") loadConflicts();
      } else if (m.type === "reload") {
        const who = m.by.split("@")[0];
        toast(m.reason === "deal record update" ? `${who} applied the newer deal record` : `${who} brought back a PowerPoint file`, "ok");
        load();
        loadConflicts();
        loadUpdate();
      }
    },
    [email, load, loadConflicts, loadUpdate, toast],
  );

  const { connected, send } = useLive(memoId, onMessage, () => {
    load();
    loadConflicts();
    loadUpdate();
  });

  const styler = useMemo(() => (view ? new Styler(view.spec) : null), [view?.spec]); // eslint-disable-line react-hooks/exhaustive-deps

  const ui: Interaction = {
    me: email,
    holders: new Map(people.filter((p) => p.editing).map((p) => [p.editing as string, p.email])),
    colors: new Map(people.map((p) => [p.email, colorFor(p.email)])),
    drafts,
    pending,
    editing,
    onPick: (el) => {
      (document.activeElement as HTMLElement | null)?.blur(); // leaving the box I hold saves it first
      const holder = people.find((p) => p.editing === el.id);
      if (holder && holder.email !== email) {
        toast(`${holder.email.split("@")[0]} is editing this box`, "warn");
        return;
      }
      wantTake.current = el.id;
      if (!send({ type: "take", element_id: el.id })) toast("Reconnecting… try again in a moment", "warn");
    },
    onDraft: (el, text) => send({ type: "draft", element_id: el.id, text }),
    onLeave: (el, text, seenRevision) => {
      setEditing(null);
      if (text !== null) {
        setPending((p) => ({ ...p, [el.id]: text }));
        // made on the revision shown when editing began: if someone saved the box meanwhile, it is kept aside
        send({ type: "op", op: { type: "set_text", target: el.id, base_revision: seenRevision, payload: { text } } });
      }
      send({ type: "release", element_id: el.id });
    },
    onFigureRemoved: () =>
      setNotice({
        title: "Figures come from the deal record",
        body: "A figure cannot be deleted or typed over in a memo. Change the value in the deal record and every memo built on it can pick it up. The box is back as it was.",
      }),
  };

  const waitSaved = async () => {
    (document.activeElement as HTMLElement | null)?.blur();
    for (let i = 0; i < 50 && Object.keys(pendingRef.current).length; i++) await new Promise((r) => setTimeout(r, 100));
  };

  const exportPptx = async () => {
    setBusy("Painting the PowerPoint…");
    await waitSaved(); // the box someone has open is saved before the export
    window.location.href = withEmail(`/memos/${memoId}/export`);
    window.setTimeout(() => setBusy(null), 2500);
  };

  const seeChanges = async () => {
    if (!update?.available) return;
    setChanges("loading");
    try {
      setChanges(await get<UpdateChanges>(`/memos/${memoId}/updates/${update.available.id}`));
    } catch (e) {
      setChanges(null);
      setNotice({ title: "Could not compare the revisions", body: (e as Error).message });
    }
  };

  const decide = async (how: "apply" | "ignore") => {
    if (!update?.available) return;
    setApplying(true);
    try {
      if (how === "apply") await waitSaved(); // the box someone has open is saved first
      const r = await post<{ status: string; reason: string | null; changed: number }>(
        `/memos/${memoId}/updates/${update.available.id}/${how}`,
      );
      if (r.status !== "applied") setNotice({ title: "Not applied", body: r.reason ?? "" });
      else if (how === "apply") {
        toast(`${r.changed} figure${r.changed === 1 ? "" : "s"} updated from record revision ${update.available.number}`, "ok");
        await load();
      }
      setChanges(null);
      loadUpdate();
    } catch (e) {
      setNotice({ title: "Not applied", body: (e as Error).message });
    } finally {
      setApplying(false);
    }
  };

  const importPptx = async (file: File) => {
    setImporting("busy");
    try {
      const form = new FormData();
      form.append("file", file);
      setImporting(await post<ImportResult>(`/memos/${memoId}/import`, form));
      loadConflicts();
    } catch (e) {
      setImporting(null);
      setNotice({ title: "Could not bring this file back", body: (e as Error).message });
    }
  };

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!view || !styler) return <main className="page"><p className="muted">Loading the memo…</p></main>;

  const open = conflicts.filter((c) => c.status === "open");
  const slideWidth = Math.min(mainWidth - 48, 1180);

  return (
    <div className="memo-page">
      <div className="memo-bar">
        <Link to="/memos" className="back" title="All memos">
          ←
        </Link>
        <div className="memo-title">
          <strong>{view.memo.deal}</strong>
          <span className="muted small">
            {view.memo.stage} · record revision {view.memo.record_revision.number} · spec v{view.spec.version.number}
          </span>
        </div>
        <div className="people">
          {people.map((p) => (
            <span
              key={p.email}
              className="avatar ring"
              style={{ background: colorFor(p.email) }}
              title={`${p.email}${p.editing ? " · editing" : ""}${p.email === email ? " (you)" : ""}`}
            >
              {p.email.slice(0, 1).toUpperCase()}
            </span>
          ))}
          <span className={`dot ${connected ? "on" : "off"}`} title={connected ? "Live" : "Reconnecting…"} />
        </div>
        <div className="actions">
          <button onClick={() => setPanel(panel ? null : "conflicts")}>
            Kept aside{open.length > 0 && <span className="count warn">{open.length}</span>}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pptx"
            hidden
            onChange={(e) => {
              if (e.target.files?.[0]) importPptx(e.target.files[0]);
              e.target.value = "";
            }}
          />
          <button onClick={() => fileRef.current?.click()}>Bring back PowerPoint</button>
          <button className="primary" onClick={exportPptx} disabled={Boolean(busy)}>
            {busy ?? "Export PowerPoint"}
          </button>
        </div>
      </div>

      {update?.available && (
        <div className="update-banner">
          <span>
            <strong>Deal record revision {update.available.number}</strong> is available
            {update.available.created_by ? ` (by ${update.available.created_by.split("@")[0]})` : ""}. This memo uses revision{" "}
            {update.current.number}.
          </span>
          <span className="actions">
            <button onClick={seeChanges}>See changes</button>
            <button onClick={() => decide("ignore")} disabled={applying}>
              Ignore
            </button>
          </span>
        </div>
      )}

      <div className="memo-body">
        <nav className="rail">
          {view.slides.map((s) => (
            <a key={s.id} href={`#slide-${s.id}`} className="rail-item">
              <span className="rail-n">{s.position + 1}</span>
              <span className="rail-name">{(s.section_id && view.spec.sections[s.section_id]?.name) || s.layout || "Slide"}</span>
            </a>
          ))}
        </nav>
        <div className="slides" ref={mainRef}>
          {slideWidth > 0 &&
            view.slides.map((s) => (
              <section key={s.id} id={`slide-${s.id}`} className="slide-block">
                <div className="slide-caption">
                  <span>{s.position + 1}</span>
                  <span>{(s.section_id && view.spec.sections[s.section_id]?.name) || ""}</span>
                  <span className="muted">{s.layout}</span>
                </div>
                <Fit width={slideWidth} pageWidthIn={view.spec.page.w_in} pageHeightIn={view.spec.page.h_in}>
                  <Slide slide={s} master={view.master} styler={styler} ui={ui} />
                </Fit>
              </section>
            ))}
        </div>
        {panel === "conflicts" && (
          <aside className="panel">
            <div className="panel-head">
              <strong>Kept aside</strong>
              <button className="link" onClick={() => setPanel(null)}>
                Close
              </button>
            </div>
            <p className="muted small">
              Edits that could not be applied without overwriting someone else's work. Nothing is lost: they stay here.
            </p>
            {conflicts.length === 0 && <p className="muted">Nothing kept aside.</p>}
            {conflicts.map((c) => (
              <div key={c.id} className="conflict">
                <div className="conflict-kind">{c.kind.replace(/_/g, " ")}</div>
                <div className="small">{c.detail}</div>
                {c.operation && (
                  <div className="muted small">
                    {c.operation.actor === "import" ? "from PowerPoint" : "in the app"}
                    {c.operation.by ? ` · ${c.operation.by}` : ""} · {new Date(c.created_at).toLocaleString()}
                  </div>
                )}
                {typeof c.operation?.payload?.text === "string" && <blockquote>{String(c.operation.payload.text)}</blockquote>}
              </div>
            ))}
          </aside>
        )}
      </div>

      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone ?? ""}`}>
            {t.text}
          </div>
        ))}
      </div>

      {notice && (
        <div className="overlay">
          <div className="dialog small-dialog" data-keep-editing>
            <h2>{notice.title}</h2>
            <p>{notice.body}</p>
            <div className="dialog-actions">
              <button
                className="primary"
                autoFocus
                onClick={() => {
                  setNotice(null);
                  window.setTimeout(() => (document.querySelector(".editor") as HTMLElement | null)?.focus(), 0);
                }}
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}

      {changes && (
        <div className="overlay" onClick={() => !applying && setChanges(null)}>
          <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
            {changes === "loading" ? (
              <p>Comparing the memo with the newer revision…</p>
            ) : (
              <>
                <h2>
                  Record revision {changes.current.number} → {changes.revision.number}
                </h2>
                <p className="muted">
                  {changes.changes.length === 0
                    ? "None of this memo's figures change. Applying only moves the memo to the newer revision."
                    : `${changes.changes.length} figure${changes.changes.length === 1 ? "" : "s"} change. Applying writes the new figures; people's words stay and nothing is deleted.`}
                </p>
                {changes.warnings.length > 0 && (
                  <div className="warnings">
                    {changes.warnings.map((w, i) => (
                      <div key={i}>
                        ⚠ Slide {w.slide} · {w.element}: {w.message}
                      </div>
                    ))}
                  </div>
                )}
                {changes.changes.length > 0 && (
                  <table className="list compact changes">
                    <thead>
                      <tr>
                        <th>Slide</th>
                        <th>Where</th>
                        <th>Before</th>
                        <th>After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.changes.map((c, i) => (
                        <tr key={i} className={c.missing ? "missing" : undefined}>
                          <td>{c.slide}</td>
                          <td>
                            {c.element}
                            <div className="muted small">{c.where}</div>
                          </td>
                          <td className="before">{c.before}</td>
                          <td className="after">
                            {c.missing && "⚠ "}
                            {c.after || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <div className="dialog-actions">
                  <button onClick={() => setChanges(null)} disabled={applying}>
                    Close
                  </button>
                  <button onClick={() => decide("ignore")} disabled={applying}>
                    Ignore
                  </button>
                  <button className="primary" onClick={() => decide("apply")} disabled={applying}>
                    {applying ? "Applying…" : "Apply"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {importing && (
        <div className="overlay">
          <div className="dialog">
            {importing === "busy" ? (
              <p>Reading the file and checking each change…</p>
            ) : (
              <>
                <h2>PowerPoint brought back</h2>
                <p className="muted">
                  {importing.changes} changes found: {importing.applied ?? 0} applied, {importing.refused ?? 0} refused,{" "}
                  {importing.kept_aside ?? 0} kept aside.
                </p>
                <ul className="import-results">
                  {importing.results.map((r, i) => (
                    <li key={i} className={r.status}>
                      <span className={`badge ${r.status}`}>{r.status.replace("_", " ")}</span> {r.type.replace(/_/g, " ")}
                      {r.reason && <div className="muted small">{r.reason}</div>}
                    </li>
                  ))}
                </ul>
                <div className="dialog-actions">
                  <button className="primary" onClick={() => setImporting(null)}>
                    Done
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
