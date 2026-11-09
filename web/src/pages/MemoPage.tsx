import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post, withEmail } from "../api/client";
import type { Conflict, El, ImportResult, MemoView, Pending, Person, Seg, SlideData, UpdateChanges, UpdateInfo } from "../api/types";
import { useAuth } from "../auth";
import { Fit, Slide, useWidth, type Interaction } from "../canvas/Slide";
import { SlideRail } from "../canvas/SlideRail";
import { AddChart, AddTable, chartChoice, tableChoice } from "../canvas/AddBox";
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
type SpecVersionInfo = { id: string; number: number; label: string | null; approved_at: string | null };
type SpecUpdate = { current: SpecVersionInfo; available: SpecVersionInfo | null };
type SpecReport = {
  from: number;
  to: number;
  slides: number;
  rebuilt: number;
  restyled_kept_words: number;
  added: number;
  removed_untouched: number;
  set_aside: string[];
  layouts_gone: string[];
  sections_gone: string[];
};
type Proposal = {
  id: string;
  summary: string | null;
  status: string;
  actor: string;
  created_at: string;
  operations: {
    op: { type: string; target?: string | null; payload?: Record<string, unknown>; label?: string };
    check: { status: string; reason: string | null; warnings: string[]; before: El | null; after: El | null };
  }[];
};

const boxText = (el: El) => el.paragraphs.map((p) => p.segments.map((x) => x.text).join("")).join("\n");
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
  const [panel, setPanel] = useState<"conflicts" | "proposals" | "agent" | "pendings" | null>(null);
  const [pendings, setPendings] = useState<Pending[]>([]);
  const pictureRef = useRef<HTMLInputElement>(null);
  const pictureFor = useRef<SlideData | null>(null);
  // what the agent works on: the slide (and box) last pointed at
  const [focus, setFocus] = useState<{ slideId: string | null; elementId: string | null; label: string | null }>({
    slideId: null,
    elementId: null,
    label: null,
  });
  const [chat, setChat] = useState<{ author: string; text: string; proposal_id: string | null }[]>([]);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [removing, setRemoving] = useState<{ chips: Seg[]; warnings: string[] | null; resolve: (remove: boolean) => void } | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  // per proposal, the operations left out by the person (all the ones that would apply are chosen by default)
  const [leftOut, setLeftOut] = useState<Record<string, number[]>>({});
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [importing, setImporting] = useState<ImportResult | "busy" | null>(null);
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  // a newer version of the firm's spec (the template) the memo could move to, and what moving would do
  const [specUpdate, setSpecUpdate] = useState<SpecUpdate | null>(null);
  const [specChanges, setSpecChanges] = useState<SpecReport | "loading" | null>(null);
  const [moving, setMoving] = useState(false);
  const [changes, setChanges] = useState<UpdateChanges | "loading" | null>(null);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // a table or chart being added to a free slide, or a person's own one being changed (el)
  const [adding, setAdding] = useState<{ slide: SlideData; zone: number[]; kind: "table" | "chart"; el?: El } | null>(null);
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

  // edits made for a person to confirm (the agent's)
  const loadChat = useCallback(() => {
    get<{ author: string; text: string; proposal_id: string | null }[]>(`/memos/${memoId}/agent`).then(setChat).catch(() => undefined);
  }, [memoId]);

  // what the memo still needs from a person: computed by the server from the memo as it is
  const pendingTimer = useRef<number | undefined>(undefined);
  const loadPendings = useCallback(() => {
    window.clearTimeout(pendingTimer.current);
    pendingTimer.current = window.setTimeout(
      () => get<Pending[]>(`/memos/${memoId}/pendings`).then(setPendings).catch(() => undefined),
      600,
    );
  }, [memoId]);

  const loadProposals = useCallback(() => {
    get<Proposal[]>(`/memos/${memoId}/proposals`).then(setProposals).catch(() => undefined);
  }, [memoId]);

  // a newer revision of the deal record than the one this memo was built from
  const loadUpdate = useCallback(() => {
    get<UpdateInfo>(`/memos/${memoId}/updates`).then(setUpdate).catch(() => undefined);
    get<SpecUpdate>(`/memos/${memoId}/spec-update`).then(setSpecUpdate).catch(() => undefined);
  }, [memoId]);

  useEffect(() => {
    load();
    loadConflicts();
    loadUpdate();
    loadProposals();
    loadChat();
    loadPendings();
  }, [load, loadConflicts, loadUpdate, loadProposals, loadChat, loadPendings]);

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
      } else if (m.type === "pendings") {
        loadPendings();
      } else if (m.type === "operation") {
        if (m.status === "applied") loadPendings();
        if (m.element) setView((v) => (v ? { ...v, slides: replaceElement(v.slides, m.element as El), memo: { ...v.memo, sequence: m.sequence } } : v));
        else if (m.slides) load();
        if (m.by === email) {
          setPending((p) => {
            const next = { ...p };
            delete next[m.target];
            return next;
          });
          (m.warnings ?? []).forEach((w) => toast(w, "warn"));
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
      } else if (m.type === "record") {
        loadUpdate();
      } else if (m.type === "proposal") {
        loadProposals();
      } else if (m.type === "reload") {
        const who = m.by.split("@")[0];
        const what: Record<string, string> = {
          "deal record update": "applied the newer deal record",
          "spec version": "moved the memo to the template's newer version",
          "slide upload": "brought in slides from a file",
        };
        toast(`${who} ${what[m.reason] ?? "brought back a PowerPoint file"}`, "ok");
        loadUpdate();
        load();
        loadPendings();
        loadConflicts();
        loadUpdate();
      }
    },
    [email, load, loadConflicts, loadUpdate, loadProposals, loadPendings, toast],
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
      setFocus({ slideId: el.slide_id, elementId: el.id, label: el.name ?? el.role ?? null });
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
    onLeave: (el, text, seenRevision, removeChips) => {
      setEditing(null);
      if (text !== null) {
        setPending((p) => ({ ...p, [el.id]: text }));
        // made on the revision shown when editing began: if someone saved the box meanwhile, it is kept aside
        const payload = removeChips.length ? { text, remove_chips: removeChips } : { text };
        send({ type: "op", op: { type: "set_text", target: el.id, base_revision: seenRevision, payload } });
      }
      send({ type: "release", element_id: el.id });
    },
    askRemove: (el, chips, text, seenRevision, removeChips) =>
      new Promise<boolean>((resolve) => {
        setRemoving({ chips, warnings: null, resolve });
        // what removing would do, checked by the server before the person decides (the firm's recipe, say)
        post<{ status: string; reason: string | null; warnings: string[] }>(`/memos/${memoId}/operations/check`, {
          type: "set_text",
          target: el.id,
          base_revision: seenRevision,
          payload: { text, remove_chips: removeChips },
        })
          .then((c) => setRemoving((r) => (r ? { ...r, warnings: c.status === "refused" ? [c.reason ?? ""] : c.warnings } : r)))
          .catch(() => setRemoving((r) => (r ? { ...r, warnings: [] } : r)));
      }),
    onFrame: (el, frame) => {
      if (!send({ type: "op", op: { type: "set_frame", target: el.id, base_revision: el.revision, payload: frame } }))
        toast("Reconnecting… try again in a moment", "warn");
    },
    onEditBox: (el) => {
      const s = view?.slides.find((x) => x.id === el.slide_id);
      const zone = s ? zoneFor(s) : null;
      if (s && zone) setAdding({ slide: s, zone, kind: el.type === "table" ? "table" : "chart", el });
    },
    onCell: (el, row, col, text) => {
      if (!send({ type: "op", op: { type: "set_cell", target: el.id, base_revision: el.revision, payload: { row, col, text } } }))
        toast("Reconnecting… try again in a moment", "warn");
    },
    onFigure: (seg) =>
      setNotice({
        title: "This figure comes from the deal record",
        body: `${seg.text} is ${seg.concept ? `the firm's ${seg.concept.replace(/_/g, " ")}` : "a value"} of the deal record${
          seg.path ? ` (${seg.path})` : ""
        }. It is never typed here: change it in the deal record and the memo offers the update.`,
      }),
    pendingIds: new Set(pendings.map((p) => p.element_id).filter((x): x is string => Boolean(x))),
    onDropPicture: (s, file, at) => addPicture(s, file, at),
    onRemoveBox: (el) => {
      if (!window.confirm("Remove this box from the slide?")) return;
      if (!send({ type: "op", op: { type: "delete_element", target: el.id, base_revision: el.revision } }))
        toast("Reconnecting… try again in a moment", "warn");
    },
    proposed: new Set(
      proposals.flatMap((pr) => pr.operations.map((o) => String(o.op.target ?? ""))).filter((t) => t),
    ),
  };

  const decideRemove = (remove: boolean) => {
    removing?.resolve(remove);
    setRemoving(null);
    window.setTimeout(() => (document.querySelector(".editor") as HTMLElement | null)?.focus(), 0);
  };

  const askAgent = async (text: string) => {
    if (!text.trim() || asking) return;
    const slideId = focus.slideId ?? view?.slides[0]?.id ?? null;
    setChat((c) => [...c, { author: "person", text, proposal_id: null }]);
    setQuestion("");
    setAsking(true);
    try {
      const r = await post<{ reply: string; proposal: { id: string } | null }>(`/memos/${memoId}/agent`, {
        message: text,
        slide_id: slideId,
        element_id: focus.elementId,
      });
      setChat((c) => [...c, { author: "agent", text: r.reply, proposal_id: r.proposal?.id ?? null }]);
      if (r.proposal) loadProposals();
    } catch (e) {
      setChat((c) => [...c, { author: "agent", text: `Something went wrong: ${(e as Error).message}`, proposal_id: null }]);
    } finally {
      setAsking(false);
    }
  };

  const chosenOf = (pr: Proposal) =>
    pr.operations.map((_o, i) => i).filter((i) => pr.operations[i].check.status === "applied" && !(leftOut[pr.id] ?? []).includes(i));

  const [deciding, setDeciding] = useState<string | null>(null); // the proposal being applied or discarded

  const decideProposal = async (id: string, how: "apply" | "discard") => {
    if (deciding) return; // one confirmation at a time: a double click does nothing
    setDeciding(id);
    try {
      await waitSaved();
      const pr = proposals.find((x) => x.id === id);
      await post(`/memos/${memoId}/proposals/${id}/${how}`, how === "apply" && pr ? { only: chosenOf(pr) } : undefined);
      loadProposals();
    } catch (e) {
      setNotice({ title: "Not done", body: (e as Error).message });
    } finally {
      setDeciding(null);
    }
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

  // the PDF for committee: the painted PowerPoint, converted; it takes a few seconds, so it is fetched and saved
  const exportPdf = async () => {
    setBusy("Making the PDF…");
    try {
      await waitSaved();
      const res = await fetch(withEmail(`/memos/${memoId}/export.pdf`));
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
      // the file name the server gives: filename*=utf-8''… (encoded) or filename="…"
      const disposition = res.headers.get("content-disposition") ?? "";
      const encoded = /filename\*=(?:utf-8|UTF-8)''([^;]+)/.exec(disposition)?.[1];
      const name = encoded ? decodeURIComponent(encoded) : /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? "memo.pdf";
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      setNotice({ title: "The PDF was not made", body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  // what an update brings: a newer record revision, or the memo's own revision when the binding changed
  const updateTarget = update?.available ?? (update?.binding_changes.length ? update.current : null);

  const seeChanges = async () => {
    if (!updateTarget) return;
    setChanges("loading");
    try {
      setChanges(await get<UpdateChanges>(`/memos/${memoId}/updates/${updateTarget.id}`));
    } catch (e) {
      setChanges(null);
      setNotice({ title: "Could not compare the revisions", body: (e as Error).message });
    }
  };

  const decide = async (how: "apply" | "ignore") => {
    if (!updateTarget) return;
    setApplying(true);
    try {
      if (how === "apply") await waitSaved(); // the box someone has open is saved first
      const r = await post<{ status: string; reason: string | null; changed: number }>(
        `/memos/${memoId}/updates/${updateTarget.id}/${how}`,
      );
      if (r.status !== "applied") setNotice({ title: "Not applied", body: r.reason ?? "" });
      else if (how === "apply") {
        toast(`${r.changed} figure${r.changed === 1 ? "" : "s"} updated`, "ok");
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

  // a newer template version: what moving does (the same move, undone), then move or stay
  const seeSpecChanges = async () => {
    if (!specUpdate?.available) return;
    setSpecChanges("loading");
    try {
      setSpecChanges(await get<SpecReport>(`/memos/${memoId}/spec-update/${specUpdate.available.id}`));
    } catch (e) {
      setSpecChanges(null);
      setNotice({ title: "Could not compare the versions", body: (e as Error).message });
    }
  };

  const decideSpec = async (how: "apply" | "ignore") => {
    if (!specUpdate?.available || moving) return;
    setMoving(true);
    try {
      if (how === "apply") await waitSaved(); // the box someone has open is saved first
      const r = await post<{ status: string; reason: string | null }>(`/memos/${memoId}/spec-update/${specUpdate.available.id}/${how}`);
      if (r.status !== "applied") setNotice({ title: "Not done", body: r.reason ?? "" });
      else if (how === "apply") {
        toast(`Moved to the template's version ${specUpdate.available.number}`, "ok");
        await load();
        loadConflicts();
        loadPendings();
      }
      setSpecChanges(null);
      loadUpdate();
    } catch (e) {
      setNotice({ title: "Not done", body: (e as Error).message });
    } finally {
      setMoving(false);
    }
  };

  // the free area of a slide the firm leaves free (people's own boxes go there), else null
  const zoneFor = (s: SlideData): number[] | null => {
    if (!view) return null;
    const sec = s.section_id ? view.spec.sections[s.section_id] : null;
    if (s.kind !== "free" && sec?.status !== "free") return null;
    return (s.layout_id && view.spec.zones?.[s.layout_id]) || null;
  };

  // a picture: stored first, then placed in the slide's free area (at the drop point, inside the area)
  const addPicture = async (s: SlideData, file: File, at?: { x_in: number; y_in: number }) => {
    const zone = zoneFor(s);
    if (!zone) return toast("Pictures go on the slides the firm leaves free", "warn");
    if (!file.type.startsWith("image/")) return toast("That is not a picture", "warn");
    try {
      const form = new FormData();
      form.append("file", file);
      const { asset_id } = await post<{ asset_id: string }>(`/memos/${memoId}/pictures`, form);
      const ratio = await new Promise<number>((resolve) => {
        const img = new Image();
        img.onload = () => resolve(img.naturalWidth / Math.max(1, img.naturalHeight));
        img.onerror = () => resolve(4 / 3);
        img.src = URL.createObjectURL(file);
      });
      const [zx, zy, zw, zh] = zone;
      let w = Math.min(4, zw);
      let h = w / ratio;
      if (h > zh) [h, w] = [zh, zh * ratio];
      const x = Math.min(Math.max(at ? at.x_in - w / 2 : zx, zx), zx + zw - w);
      const y = Math.min(Math.max(at ? at.y_in - h / 2 : zy, zy), zy + zh - h);
      addBox(s, { type: "image", asset_id, alt: file.name, x_in: x, y_in: y, w_in: w, h_in: h }, Boolean(at));
    } catch (e) {
      setNotice({ title: "The picture was not added", body: (e as Error).message });
    }
  };

  // one way in for a PowerPoint or a PDF; the file says what it is: this memo's export, edited, is brought back
  // (its changes applied, what cannot be kept aside); any other file adds its slides or pages as free slides
  // (native where possible, flagged pictures where not; what did not come in is said)
  type SlidesIn = { kind: "slides"; slides: number; boxes: Record<string, number>; pictures: number; skipped: string[]; refused: string[] };
  const uploadFile = async (file: File) => {
    setUploading(true);
    try {
      await waitSaved();
      const form = new FormData();
      form.append("file", file);
      const out = await post<SlidesIn | (ImportResult & { kind: "bring_back" })>(`/memos/${memoId}/upload`, form);
      if (out.kind === "bring_back") {
        setImporting(out);
        loadConflicts();
        loadPendings();
        return;
      }
      const r = out;
      const boxes = Object.entries(r.boxes).map(([k, v]) => `${v} ${k}`).join(", ");
      const came = `${r.slides} slide${r.slides === 1 ? "" : "s"} added${boxes ? ` · ${boxes}` : ""}${r.pictures ? ` · ${r.pictures} picture${r.pictures === 1 ? "" : "s"} to confirm` : ""}`;
      if (r.skipped.length || r.refused.length)
        setNotice({
          title: came,
          body: [...r.skipped.map((x) => `Not brought in: ${x}`), ...r.refused.map((x) => `Refused: ${x}`)].join("\n"),
        });
      else toast(came, "ok");
      await load();
      loadPendings();
    } catch (e) {
      setNotice({ title: "The file was not brought in", body: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const dismissPending = async (p: Pending) => {
    try {
      await post(`/memos/${memoId}/pendings/dismiss`, { kind: p.kind, target_id: p.target_id });
      loadPendings();
    } catch (e) {
      setNotice({ title: "Not accepted", body: (e as Error).message });
    }
  };

  // placed: the position is the person's (a drop), not the next free spot
  const addBox = (picked: SlideData, payload: Record<string, unknown>, placed = false) => {
    const editing = adding?.el;
    setAdding(null);
    if (editing) {
      // the same box built again from the record; what the person wrote on it stays
      if (!send({ type: "op", op: { type: "edit_box", target: editing.id, base_revision: editing.revision, payload } }))
        toast("Reconnecting… try again in a moment", "warn");
      return;
    }
    const s = view?.slides.find((x) => x.id === picked.id) ?? picked; // as it is now, with the boxes added since
    // each new box a little lower and to the right of the last one, so none hides another; then it is dragged
    const zone = zoneFor(s);
    const own = s.elements.filter((e) => e.firm_lock === "free").length;
    if (zone && !placed) {
      const [zx, zy, zw, zh] = zone;
      const w = Math.min(Number(payload.w_in), zw);
      const h = Math.min(Number(payload.h_in), zh);
      const step = (own % 6) * 0.3;
      payload = { ...payload, w_in: w, h_in: h, x_in: Math.min(zx + step, zx + zw - w), y_in: Math.min(zy + step, zy + zh - h) };
    }
    if (!send({ type: "op", op: { type: "add_element", target: s.id, payload: { slide_id: s.id, ...payload } } }))
      toast("Reconnecting… try again in a moment", "warn");
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
          <button className={panel === "agent" ? "active-toggle" : ""} onClick={() => setPanel(panel === "agent" ? null : "agent")}>
            Agent
          </button>
          {proposals.length > 0 && (
            <button onClick={() => setPanel(panel === "proposals" ? null : "proposals")}>
              Proposals<span className="count">{proposals.length}</span>
            </button>
          )}
          <button onClick={() => setPanel(panel === "pendings" ? null : "pendings")}>
            Pendings{pendings.length > 0 && <span className="count">{pendings.length}</span>}
          </button>
          <input
            ref={pictureRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file && pictureFor.current) addPicture(pictureFor.current, file);
              e.target.value = "";
            }}
          />
          <button onClick={() => setPanel(panel === "conflicts" ? null : "conflicts")}>
            Kept aside{open.length > 0 && <span className="count warn">{open.length}</span>}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pptx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation"
            hidden
            onChange={(e) => {
              if (e.target.files?.[0]) uploadFile(e.target.files[0]);
              e.target.value = "";
            }}
          />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}
                  title="This memo's PowerPoint, edited, comes back with its changes; any other PowerPoint or PDF adds its slides">
            {uploading ? "Bringing it in…" : "Upload PowerPoint or PDF"}
          </button>
          <button onClick={exportPdf} disabled={Boolean(busy)}>
            {busy === "Making the PDF…" ? busy : "Export PDF"}
          </button>
          <button className="primary" onClick={exportPptx} disabled={Boolean(busy)}>
            {busy && busy !== "Making the PDF…" ? busy : "Export PowerPoint"}
          </button>
        </div>
      </div>

      {update && updateTarget && (
        <div className="update-banner">
          {update.available ? (
            <span>
              <strong>Deal record revision {update.available.number}</strong> is available
              {update.available.created_by ? ` (by ${update.available.created_by.split("@")[0]})` : ""}. This memo uses revision{" "}
              {update.current.number}.
            </span>
          ) : (
            <span>
              <strong>Where some figures come from changed</strong> ({update.binding_changes.map((c) => c.replace(/_/g, " ")).join(", ")}
              ). This memo still shows the earlier figures.
            </span>
          )}
          <span className="actions">
            <button onClick={seeChanges}>See changes</button>
            <button onClick={() => decide("ignore")} disabled={applying}>
              Ignore
            </button>
          </span>
        </div>
      )}

      {specUpdate?.available && (
        <div className="update-banner spec-banner">
          <span>
            <strong>The firm's template has version {specUpdate.available.number}</strong>
            {specUpdate.available.label ? ` (${specUpdate.available.label})` : ""}. This memo uses version {specUpdate.current.number}.
          </span>
          <span className="actions">
            <button onClick={seeSpecChanges}>See changes</button>
            <button onClick={() => decideSpec("ignore")} disabled={moving}>
              Ignore
            </button>
          </span>
        </div>
      )}

      {specChanges && (
        <div className="overlay" onClick={() => !moving && setSpecChanges(null)}>
          <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
            {specChanges === "loading" ? (
              <p className="muted">Working out what moving would do…</p>
            ) : (
              <>
                <h2>
                  Template version {specChanges.from} → {specChanges.to}
                </h2>
                <p className="muted">Moving never deletes anyone's work: what has no place in the new version is kept aside or stays pending.</p>
                <ul className="spec-report">
                  <li>{specChanges.slides} slides go to the new version's layouts</li>
                  <li>{specChanges.rebuilt} boxes nobody edited are built again as the new version draws them</li>
                  {specChanges.restyled_kept_words > 0 && (
                    <li>{specChanges.restyled_kept_words} boxes people wrote keep their words, with the new style and place</li>
                  )}
                  {specChanges.added > 0 && <li>{specChanges.added} boxes the new version adds</li>}
                  {specChanges.removed_untouched > 0 && <li>{specChanges.removed_untouched} template boxes nobody touched are gone in the new version</li>}
                </ul>
                {(specChanges.set_aside.length > 0 || specChanges.layouts_gone.length > 0 || specChanges.sections_gone.length > 0) && (
                  <>
                    <h3>Needs a person</h3>
                    <ul className="spec-report warn">
                      {specChanges.set_aside.map((x) => (
                        <li key={x}>{x}: not in the new version; what it held is kept aside</li>
                      ))}
                      {specChanges.layouts_gone.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                      {specChanges.sections_gone.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="dialog-actions">
                  <button onClick={() => setSpecChanges(null)} disabled={moving}>
                    Close
                  </button>
                  <button onClick={() => decideSpec("ignore")} disabled={moving}>
                    Ignore
                  </button>
                  <button className="primary" onClick={() => decideSpec("apply")} disabled={moving}>
                    {moving ? "Moving…" : `Move to version ${specChanges.to}`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <div className="memo-body">
        <SlideRail
          memoId={memoId}
          slides={view.slides}
          spec={view.spec}
          focusId={focus.slideId}
          onFocus={(id) => setFocus({ slideId: id, elementId: null, label: null })}
          onOp={(op) => {
            if (!send({ type: "op", op })) toast("Reconnecting… try again in a moment", "warn");
          }}
          onUpload={uploadFile}
          uploading={uploading}
        />
        <div className="slides" ref={mainRef}>
          {slideWidth > 0 &&
            view.slides.map((s) => {
              const zone = zoneFor(s);
              return (
                <section key={s.id} id={`slide-${s.id}`} className="slide-block">
                  <div className="slide-caption">
                    <span>{s.position + 1}</span>
                    <span>{(s.section_id && view.spec.sections[s.section_id]?.name) || ""}</span>
                    <span className="muted">{s.layout}</span>
                    {zone && (
                      <span className="add-boxes">
                        <span className="muted">Free slide · add</span>
                        <button
                          className="link"
                          onClick={() =>
                            addBox(s, { type: "text", text: "", x_in: zone[0], y_in: zone[1], w_in: Math.min(zone[2], 4), h_in: Math.min(zone[3], 1) })
                          }
                        >
                          Text
                        </button>
                        <button className="link" onClick={() => setAdding({ slide: s, zone, kind: "table" })}>
                          Table
                        </button>
                        <button className="link" onClick={() => setAdding({ slide: s, zone, kind: "chart" })}>
                          Chart
                        </button>
                        <button
                          className="link"
                          onClick={() => {
                            pictureFor.current = s;
                            pictureRef.current?.click();
                          }}
                        >
                          Image
                        </button>
                      </span>
                    )}
                  </div>
                  <Fit width={slideWidth} pageWidthIn={view.spec.page.w_in} pageHeightIn={view.spec.page.h_in}>
                    <Slide slide={s} master={view.master} styler={styler} ui={ui} zone={zone} />
                  </Fit>
                </section>
              );
            })}
        </div>
        {panel === "agent" && (
          <aside className="panel agent-panel">
            <div className="panel-head">
              <strong>Agent</strong>
              <button className="link" onClick={() => setPanel(null)}>
                Close
              </button>
            </div>
            <p className="muted small">
              It works on one slide, with the firm's spec and the deal record's figures, and proposes; nothing changes until you apply.
              Figures are never typed: they come from the record.
            </p>
            <div className="agent-scope small">
              On slide {(view.slides.find((x) => x.id === (focus.slideId ?? view.slides[0]?.id))?.position ?? 0) + 1}
              {focus.elementId ? ` · box "${focus.label ?? "selected"}"` : " · the whole slide"}
              {focus.elementId && (
                <button className="link" onClick={() => setFocus({ ...focus, elementId: null, label: null })}>
                  {" "}
                  (whole slide)
                </button>
              )}
            </div>
            <div className="agent-chat">
              {chat.length === 0 && (
                <div className="agent-try">
                  {["Fill the boxes the model should write on this slide", "Write the title as a one-line conclusion", "Shorten the text of this slide"].map((t) => (
                    <button key={t} onClick={() => askAgent(t)} disabled={asking}>
                      {t}
                    </button>
                  ))}
                </div>
              )}
              {chat.map((m, i) => (
                <div key={i} className={`msg ${m.author}`}>
                  {m.text}
                  {m.proposal_id && proposals.some((pr) => pr.id === m.proposal_id) && (
                    <button className="link" onClick={() => setPanel("proposals")}>
                      {" "}
                      See the proposal
                    </button>
                  )}
                </div>
              ))}
              {asking && <div className="msg agent muted">Working on it… (about half a minute)</div>}
            </div>
            <form
              className="agent-ask"
              onSubmit={(e) => {
                e.preventDefault();
                askAgent(question);
              }}
            >
              <textarea
                rows={3}
                value={question}
                placeholder="Ask for an edit of this slide"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    askAgent(question);
                  }
                }}
              />
              <button className="primary" disabled={asking || !question.trim()}>
                Ask
              </button>
            </form>
          </aside>
        )}
        {panel === "proposals" && (
          <aside className="panel">
            <div className="panel-head">
              <strong>Proposals</strong>
              <button className="link" onClick={() => setPanel(null)}>
                Close
              </button>
            </div>
            <p className="muted small">
              Edits made for you to confirm. Nothing changes until you apply; a box someone edits meanwhile is kept aside, not overwritten.
            </p>
            {proposals.map((pr) => (
              <div key={pr.id} className="proposal">
                <strong>{pr.summary ?? "Proposal"}</strong>
                <div className="muted small">
                  {pr.actor} · {new Date(pr.created_at).toLocaleString()}
                </div>
                {pr.operations.map((o, i) => (
                  <div key={i} className={`proposal-op ${o.check.status}${(leftOut[pr.id] ?? []).includes(i) ? " left-out" : ""}`}>
                    <div className="small">
                      <input
                        type="checkbox"
                        disabled={o.check.status !== "applied"}
                        checked={o.check.status === "applied" && !(leftOut[pr.id] ?? []).includes(i)}
                        onChange={(e) =>
                          setLeftOut((l) => {
                            const now = new Set(l[pr.id] ?? []);
                            if (e.target.checked) now.delete(i);
                            else now.add(i);
                            return { ...l, [pr.id]: [...now] };
                          })
                        }
                        title={o.check.status === "applied" ? "Apply this change" : "This change would be refused"}
                      />{" "}
                      <span className={`badge ${o.check.status}`}>{o.check.status === "applied" ? "would apply" : o.check.status.replace("_", " ")}</span>{" "}
                      {o.op.label ?? o.op.type.replace(/_/g, " ")}
                      {!o.op.label && (o.check.after?.name ? ` · ${o.check.after.name}` : o.check.before?.name ? ` · ${o.check.before.name}` : "")}
                    </div>
                    {o.check.reason && <div className="muted small">{o.check.reason}</div>}
                    {o.check.before && o.check.after && (
                      <div className="before-after">
                        <div className="before">{boxText(o.check.before)}</div>
                        <div className="after">{boxText(o.check.after)}</div>
                      </div>
                    )}
                    {o.check.warnings.map((w, j) => (
                      <div key={j} className="small flag missing">
                        ⚠ {w}
                      </div>
                    ))}
                  </div>
                ))}
                <div className="dialog-actions">
                  <button disabled={deciding !== null} onClick={() => decideProposal(pr.id, "discard")}>
                    Discard
                  </button>
                  <button
                    className="primary"
                    disabled={deciding !== null || chosenOf(pr).length === 0}
                    onClick={() => decideProposal(pr.id, "apply")}
                  >
                    {deciding === pr.id
                      ? "Applying…"
                      : chosenOf(pr).length === pr.operations.length
                        ? "Apply"
                        : `Apply ${chosenOf(pr).length} of ${pr.operations.length}`}
                  </button>
                </div>
              </div>
            ))}
          </aside>
        )}
        {panel === "pendings" && (
          <aside className="panel">
            <div className="panel-head">
              <strong>Pendings</strong>
              <button className="link" onClick={() => setPanel(null)}>
                Close
              </button>
            </div>
            <p className="muted small">
              What the memo still needs from a person. Nothing here blocks editing or exporting; each goes away when the memo has
              it, or when someone accepts it.
            </p>
            {pendings.length === 0 && <p className="muted">Nothing pending.</p>}
            {pendings.map((p, i) => (
              <div key={`${p.kind}-${p.target_id}-${i}`} className="pending">
                <button
                  className="link pending-where"
                  onClick={() => {
                    if (!p.slide_id) return;
                    document.getElementById(`slide-${p.slide_id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
                    setFocus({ slideId: p.slide_id, elementId: p.element_id, label: p.where });
                  }}
                >
                  {p.slide ? `Slide ${p.slide}` : "Memo"}
                  {p.where ? ` · ${p.where}` : ""}
                </button>
                <div className="pending-kind">{p.kind.replace(/_/g, " ")}</div>
                <div className="small">{p.message}</div>
                {p.dismissable && (
                  <button className="small-button" onClick={() => dismissPending(p)}>
                    It's fine
                  </button>
                )}
              </div>
            ))}
          </aside>
        )}
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

      {removing && (
        <div className="overlay">
          <div className="dialog small-dialog" data-keep-editing>
            <h2>Remove {removing.chips.length === 1 ? "this figure" : "these figures"} from the box?</h2>
            <p>
              {removing.chips.map((c) => `${c.text} (${(c.concept ?? "figure").replace(/_/g, " ")})`).join(", ")}. A figure is never typed
              over; it can be taken out of a box, and put back later by its name.
            </p>
            {removing.warnings === null ? (
              <p className="muted small">Checking with the firm's spec…</p>
            ) : (
              removing.warnings.map((w, i) => (
                <div key={i} className="warnings">
                  ⚠ {w}
                </div>
              ))
            )}
            <div className="dialog-actions">
              <button autoFocus onClick={() => decideRemove(false)}>
                Put it back
              </button>
              <button className="primary" onClick={() => decideRemove(true)}>
                Remove it
              </button>
            </div>
          </div>
        </div>
      )}

      {adding?.kind === "table" && (
        <AddTable
          memoId={memoId}
          zone={adding.zone}
          initial={adding.el ? tableChoice(adding.el) : null}
          onAdd={(p) => addBox(adding.slide, p)}
          onClose={() => setAdding(null)}
        />
      )}
      {adding?.kind === "chart" && (
        <AddChart
          memoId={memoId}
          zone={adding.zone}
          initial={adding.el ? chartChoice(adding.el) : null}
          onAdd={(p) => addBox(adding.slide, p)}
          onClose={() => setAdding(null)}
        />
      )}

      {notice && (
        <div className="overlay">
          <div className="dialog small-dialog" data-keep-editing>
            <h2>{notice.title}</h2>
            <p className="notice-body">{notice.body}</p>
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
                  {changes.current.id === changes.revision.id
                    ? "Figures from where the binding now says"
                    : `Record revision ${changes.current.number} → ${changes.revision.number}`}
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
