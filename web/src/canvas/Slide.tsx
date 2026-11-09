import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { withEmail } from "../api/client";
import type { El, Paragraph, Seg, SlideData } from "../api/types";
import { Chart } from "./Chart";
import { isNoData, isSlot, plainText, ptPx, px, rebuild, type Styler } from "./style";

// One slide, painted from the memo's data at 96 px per inch; the page scales it to fit.

export type Interaction = {
  me: string;
  holders: Map<string, string>; // element id → email of who is editing it
  colors: Map<string, string>; // email → colour
  drafts: Record<string, string>; // element id → text being typed by someone else
  pending: Record<string, string>; // element id → my text, saved but not yet confirmed
  editing: string | null; // the box I hold
  onPick: (el: El) => void;
  onDraft: (el: El, text: string) => void;
  onLeave: (el: El, text: string | null, seenRevision: number, removeChips: string[]) => void; // the revision it was made on
  // a figure was deleted while typing: ask whether to take it out of the box (true) or put it back (false)
  askRemove: (el: El, chips: Seg[], text: string, seenRevision: number, removeChips: string[]) => Promise<boolean>;
  proposed?: Set<string>; // boxes an open proposal would change
  onFrame?: (el: El, frame: { x_in: number; y_in: number; w_in: number; h_in: number }) => void; // a box moved or resized
  onRemoveBox?: (el: El) => void;
  onEditBox?: (el: El) => void; // a person's own table or chart: its rows, columns, figures, kind
  onCell?: (el: El, row: number, col: number, text: string) => void; // a table cell's words written
  onFigure?: (seg: Seg) => void; // a figure clicked: it comes from the deal record
  pendingIds?: Set<string>; // boxes with something a person must still do or accept
  onDropPicture?: (slide: SlideData, file: File, at: { x_in: number; y_in: number }) => void; // a picture dropped on a free slide
};

function Segments({ segments, page }: { segments: Seg[]; page: number }) {
  return (
    <>
      {segments.map((s, i) => {
        if (s.kind === "page_number") return <span key={i}>{page}</span>;
        if (s.kind === "chip")
          return (
            <span
              key={i}
              className={`chip${isNoData(s) ? " nodata" : ""}`}
              title={`${s.concept ?? "figure"} · from the deal record${s.path ? ` (${s.path})` : ""}`}
            >
              {s.text}
            </span>
          );
        if (isSlot(s))
          return (
            <span key={i} className={`slot ${s.kind}`} title={s.kind === "model_draft" ? "to write" : s.kind.replace("_", " ")}>
              {s.text}
            </span>
          );
        return <span key={i}>{s.text}</span>;
      })}
    </>
  );
}

function Paragraphs({ el, paragraphs, styler, page }: { el: El; paragraphs: Paragraph[]; styler: Styler; page: number }) {
  return (
    <>
      {paragraphs.map((p, i) => (
        <p key={i} style={styler.paragraphCss(p, el)} data-bullet={p.style.bullet_char || undefined}>
          <Segments segments={p.segments} page={page} />
          {p.segments.every((s) => !s.text) && <br />}
        </p>
      ))}
    </>
  );
}

function anchorOf(paragraphs: Paragraph[]): CSSProperties["justifyContent"] {
  const a = paragraphs[0]?.style.anchor;
  return a === "middle" ? "center" : a === "bottom" ? "flex-end" : "flex-start";
}

function box(el: El): CSSProperties {
  const f = el.frame;
  return {
    left: px(f.x_in),
    top: px(f.y_in),
    width: px(f.w_in),
    height: px(f.h_in),
    transform: f.rotation_deg ? `rotate(${f.rotation_deg}deg)` : undefined,
    zIndex: f.z_index || undefined,
  };
}

// --- the text editor: the box I hold -------------------------------------------------------------------
// Figures (chips) cannot be typed into. Deleting one asks first: taken out of the box on purpose, or put back.
// Leaving the box (click elsewhere, Escape) saves it through the door.
function Editor({ el, styler, page, ui }: { el: El; styler: Styler; page: number; ui: Interaction }) {
  const ref = useRef<HTMLDivElement>(null);
  const start = useRef(plainText(el.paragraphs));
  const seen = useRef(el.revision); // the edit is made on what was shown when editing began
  const good = useRef<Paragraph[]>(el.paragraphs);
  const [shown, setShown] = useState<{ version: number; paragraphs: Paragraph[] }>({ version: 0, paragraphs: el.paragraphs });
  const removed = useRef<Set<string>>(new Set()); // figures taken out on purpose
  const asking = useRef(false);
  const typed = useRef(false);

  // someone's save of this box arrived after it was opened: if nothing was typed yet, start again from it
  useEffect(() => {
    if (typed.current || el.revision === seen.current) return;
    start.current = plainText(el.paragraphs);
    seen.current = el.revision;
    good.current = el.paragraphs;
    setShown((v) => ({ version: v.version + 1, paragraphs: el.paragraphs }));
  }, [el.revision, el.paragraphs]);

  const read = () => Array.from(ref.current?.children ?? []).map((c) => (c.textContent ?? "").replace(/\u00a0/g, " ")).join("\n");

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.focus();
    const range = document.createRange();
    range.selectNodeContents(root);
    range.collapse(false);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  }, [shown.version]);

  const onInput = async () => {
    if (asking.current) return;
    const text = read();
    const present = new Set(Array.from(ref.current?.querySelectorAll("[data-chip-id]") ?? []).map((n) => n.getAttribute("data-chip-id")));
    const expected = good.current.flatMap((p) => p.segments.filter((s) => s.kind === "chip" && s.id && !removed.current.has(s.id)));
    const gone = expected.filter((s) => !present.has(s.id ?? ""));
    if (gone.length) {
      asking.current = true;
      const remove = await ui.askRemove(el, gone, text, seen.current, [...removed.current, ...gone.map((s) => s.id as string)]);
      asking.current = false;
      if (!remove) {
        setShown((v) => ({ version: v.version + 1, paragraphs: good.current }));
        return;
      }
      gone.forEach((s) => removed.current.add(s.id as string));
    }
    const rebuilt = rebuild(text, good.current, removed.current);
    if (rebuilt === null) {
      setShown((v) => ({ version: v.version + 1, paragraphs: good.current }));
      return;
    }
    good.current = rebuilt;
    typed.current = true;
    ui.onDraft(el, text);
  };

  return (
    <div
      key={shown.version}
      ref={ref}
      className="editor"
      contentEditable
      suppressContentEditableWarning
      spellCheck
      onInput={onInput}
      onBlur={(e) => {
        // the figure warning keeps the box: the person goes back to it after reading
        if ((e.relatedTarget as HTMLElement | null)?.closest("[data-keep-editing]")) return;
        const text = read();
        ui.onLeave(el, text === start.current && !removed.current.size ? null : text, seen.current, [...removed.current]);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") (e.target as HTMLElement).blur();
      }}
      onPaste={(e) => {
        e.preventDefault(); // text only: styles belong to the firm
        document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
      }}
    >
      {shown.paragraphs.map((p, i) => (
        <p key={i} style={styler.paragraphCss(p, el)} data-bullet={p.style.bullet_char || undefined}>
          {p.segments.map((s, j) =>
            s.kind === "chip" ? (
              <span key={j} data-chip data-chip-id={s.id ?? ""} contentEditable={false} className={`chip${isNoData(s) ? " nodata" : ""}`}>
                {s.text}
              </span>
            ) : s.kind === "page_number" ? (
              <span key={j}>{page}</span>
            ) : (
              <span key={j} className={isSlot(s) ? `slot ${s.kind}` : undefined}>
                {s.text}
              </span>
            ),
          )}
          {p.segments.every((s) => !s.text) && <br />}
        </p>
      ))}
    </div>
  );
}

// --- elements -----------------------------------------------------------------------------------------
function TextEl({ el, styler, page, ui }: { el: El; styler: Styler; page: number; ui: Interaction }) {
  const holder = ui.holders.get(el.id);
  const mine = ui.editing === el.id;
  const other = holder && holder !== ui.me ? holder : null;
  const draft = other ? ui.drafts[el.id] : undefined;
  const pendingText = ui.pending[el.id];
  const shown =
    (draft !== undefined && rebuild(draft, el.paragraphs, undefined, true)) ||
    (pendingText !== undefined && rebuild(pendingText, el.paragraphs, undefined, true)) ||
    el.paragraphs;
  const editable = !el.master && el.firm_lock !== "fixed";
  const fill = styler.color(el.fill);
  const line = styler.color(el.line);
  // the template's insets for this box (the same the fit measure and the PowerPoint use)
  const ins = el.inset ?? (el.placeholder ? [0.1, 0.05, 0.1, 0.05] : null);
  const inset = ins ? { padding: `${px(ins[1])}px ${px(ins[2])}px ${px(ins[3])}px ${px(ins[0])}px` } : {};
  const overflow = el.fit !== undefined && !el.fit.fits;
  const color = other ? ui.colors.get(other) : undefined;
  return (
    <div
      className={`el text${editable ? " editable" : ""}${mine ? " mine" : ""}${other ? " held" : ""}${pendingText !== undefined ? " saving" : ""}${ui.proposed?.has(el.id) ? " proposed" : ""}${overflow ? " overflow" : ""}`}
      style={{
        ...box(el),
        ...inset,
        background: fill ?? undefined,
        border: line ? `${Math.max(ptPx(el.line_width_pt ?? 0.75), 1)}px solid ${line}` : undefined,
        justifyContent: anchorOf(el.paragraphs),
        ["--held" as string]: color,
      }}
      onMouseDown={(e) => {
        if (!editable || mine) return;
        e.preventDefault();
        ui.onPick(el);
      }}
      title={el.description ?? undefined}
    >
      {mine ? <Editor el={el} styler={styler} page={page} ui={ui} /> : <Paragraphs el={el} paragraphs={shown} styler={styler} page={page} />}
      {other && <span className="holder-tag" style={{ background: color }}>{other.split("@")[0]}</span>}
      {overflow && !mine && <span className="overflow-tag">Doesn't fit</span>}
    </div>
  );
}

function TableEl({ el, styler, page, ui }: { el: El; styler: Styler; page: number; ui: Interaction }) {
  const t = el.table!;
  const [open, setOpen] = useState<string | null>(null); // the cell being written: "row|col"
  // words can be written; figures come from the record. The firm's header and labels stay; in a person's
  // own table every word is theirs and every data cell the record's
  const writable = (c: { kind: string } | undefined, hasFigure: boolean) =>
    Boolean(ui.onCell) && !el.master && el.firm_lock !== "fixed" && !hasFigure && c !== undefined &&
    (t.own ? c.kind !== "data" : c.kind !== "fixed");
  const st = styler.spec.table_style ?? {};
  const role = styler.tableRole();
  const font = role?.font ?? styler.spec.fonts.tables ?? styler.spec.fonts.body;
  const size = ptPx(el.fit?.size_pt && el.fit.scale < 1 ? el.fit.size_pt : role?.size_pt ?? 10);
  const cells = new Map(t.cells.map((c) => [`${c.row}|${c.col}`, c]));
  const headerFill = styler.color(st.header_fill_ref as string);
  const headerText = styler.color(st.header_text_ref as string);
  const bodyText = styler.color(st.body_text_ref as string) ?? styler.color(role?.color_ref) ?? "#222";
  const banding = styler.color(st.banding_ref as string);
  const rule = styler.color(st.rule_color_ref as string) ?? "#d0d0d0";
  return (
    <div className={`el table${el.fit && !el.fit.fits ? " overflow" : ""}`} style={box(el)}>
      {el.fit && !el.fit.fits && <span className="overflow-tag">Doesn't fit</span>}
      <table style={{ fontFamily: font ? `'${font}', Arial, sans-serif` : undefined, fontSize: size, color: bodyText }}>
        <colgroup>
          {t.columns.map((w, i) => (
            <col key={i} style={{ width: w ? px(w) : undefined }} />
          ))}
        </colgroup>
        <tbody>
          {t.rows.map((r, ri) => (
            <tr key={ri} style={{ height: r.height_in ? px(r.height_in) : undefined }}>
              {t.columns.map((_, ci) => {
                const c = cells.get(`${ri}|${ci}`);
                const header = r.kind === "header";
                const fill = styler.color(c?.fill) ?? (header ? headerFill : r.kind === "data" && ri % 2 === 0 ? banding : null);
                const text = (c?.paragraphs ?? []).map((p) => p.segments);
                const figure = text.flat().find((x) => x.kind === "chip");
                const key = `${ri}|${ci}`;
                const canWrite = writable(c, Boolean(figure));
                const plain = text.map((segs) => segs.map((x) => x.text).join("")).join("\n");
                return (
                  <td
                    key={ci}
                    className={canWrite ? "cell-writable" : figure ? "cell-figure" : undefined}
                    onClick={() => (figure ? ui.onFigure?.(figure) : canWrite ? setOpen(key) : undefined)}
                    style={{
                      background: fill ?? undefined,
                      color: header ? headerText ?? bodyText : bodyText,
                      fontWeight: (header && st.header_bold) || r.kind === "group" || r.kind === "total" ? 700 : 400,
                      textAlign: ci > 0 && !header && st.number_align ? (st.number_align as CSSProperties["textAlign"]) : undefined,
                      borderBottom: `1px solid ${rule}`,
                    }}
                  >
                    {open === key ? (
                      <input
                        className="cell-input"
                        defaultValue={plain}
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                          if (e.key === "Escape") setOpen(null);
                        }}
                        onBlur={(e) => {
                          setOpen(null);
                          if (e.target.value !== plain) ui.onCell?.(el, ri, ci, e.target.value);
                        }}
                      />
                    ) : (
                      text.map((segs, i) => (
                        <div key={i}>
                          <Segments segments={segs} page={page} />
                        </div>
                      ))
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ElementView({ el, styler, page, ui }: { el: El; styler: Styler; page: number; ui: Interaction }) {
  if (el.type === "text") return <TextEl el={el} styler={styler} page={page} ui={ui} />;
  if (el.type === "table" && el.table) return <TableEl el={el} styler={styler} page={page} ui={ui} />;
  if ((el.type === "table" && !el.table) || (el.type === "chart" && !el.chart))
    return (
      <div className="el placeholder-box" style={box(el)}>
        <span>
          {el.type === "table" ? "Table" : "Chart"} · {el.description ?? el.name}
        </span>
      </div>
    );
  if (el.type === "chart" && el.chart)
    return (
      <div className="el chart" style={box(el)}>
        <Chart chart={el.chart} width={px(el.frame.w_in)} height={px(el.frame.h_in)} styler={styler} />
      </div>
    );
  if (el.type === "line") {
    const w = px(el.frame.w_in);
    const h = px(el.frame.h_in);
    const stroke = Math.max(ptPx(el.line_width_pt ?? 0.75), 1);
    return (
      <svg className="el line" style={{ ...box(el), width: Math.max(w, stroke), height: Math.max(h, stroke), overflow: "visible" }}>
        <line x1={0} y1={0} x2={w} y2={h} stroke={styler.color(el.line) ?? "#9a9a9a"} strokeWidth={stroke} />
      </svg>
    );
  }
  if (el.type === "image") {
    if (el.image)
      return (
        <div className={`el image picture${ui.pendingIds?.has(el.id) ? " to-confirm" : ""}`} style={box(el)}>
          <img src={withEmail(`/elements/${el.id}/image`)} alt={el.image.alt ?? "picture"} draggable={false} />
          {ui.pendingIds?.has(el.id) && <span className="picture-flag">Picture to confirm</span>}
        </div>
      );
    if (`${el.role ?? ""} ${el.name ?? ""}`.toLowerCase().includes("logo"))
      return (
        <div className="el image" style={box(el)}>
          <img src={withEmail(`/elements/${el.id}/image`)} alt={el.name ?? "logo"} />
        </div>
      );
    return (
      <div className="el image placeholder-box" style={box(el)}>
        <span>{el.description ?? el.name ?? "Picture"}</span>
      </div>
    );
  }
  const fill = styler.color(el.fill);
  const line = styler.color(el.line);
  return (
    <div
      className="el shape"
      style={{
        ...box(el),
        background: fill ?? undefined,
        border: line ? `${Math.max(ptPx(el.line_width_pt ?? 0.75), 1)}px solid ${line}` : undefined,
        borderRadius: el.shape_kind === "ellipse" ? "50%" : el.shape_kind === "rounded_rect" ? 8 : undefined,
      }}
    />
  );
}

type Frame4 = { x_in: number; y_in: number; w_in: number; h_in: number };

// a person's own table or chart built from the record (its rows, columns, figures can be chosen again); one that
// came with its own data (an uploaded slide's) is not
const boundToRecord = (el: El) =>
  Boolean(el.table?.own && el.table.cells.some((c) => c.paragraphs.some((p) => p.segments.some((x) => x.path)))) ||
  Boolean(el.chart?.own && el.chart.series.some((x) => x.path));
const SNAP = 0.1;
const snapTo = (v: number) => Math.round(v / SNAP) * SNAP;

export function Slide({
  slide,
  master,
  styler,
  ui,
  zone,
}: {
  slide: SlideData;
  master: El[];
  styler: Styler;
  ui: Interaction;
  zone?: number[] | null; // the free area of a slide the firm leaves free: boxes there move and resize
}) {
  const page = slide.position + 1;
  const { w_in, h_in } = styler.spec.page;
  // the frame shown while dragging, and after release until the saved box arrives (a refusal puts it back)
  const [drag, setDrag] = useState<{ id: string; frame: Frame4; revision: number } | null>(null);
  // the boxes inside the free area move; the header and footer around it stay where the firm puts them
  const movable = (el: El) => {
    if (!zone || el.master || el.firm_lock === "fixed" || el.type === "line") return false;
    const [zx, zy, zw, zh] = zone;
    const f = el.frame;
    return f.x_in >= zx - 0.05 && f.y_in >= zy - 0.05 && f.x_in + f.w_in <= zx + zw + 0.05 && f.y_in + f.h_in <= zy + zh + 0.05;
  };
  const shown = (el: El) => (drag?.id === el.id && drag.revision === el.revision ? { ...el, frame: { ...el.frame, ...drag.frame } } : el);

  // drag a grip: the box follows (snapped, inside the zone); on release the door decides
  const start = (e: React.PointerEvent, el: El, mode: "move" | "resize") => {
    if (!zone) return;
    e.preventDefault();
    e.stopPropagation();
    const paper = (e.currentTarget as HTMLElement).closest(".slide-paper") as HTMLElement;
    const scale = paper.getBoundingClientRect().width / paper.offsetWidth;
    const [zx, zy, zw, zh] = zone;
    const from = { x: e.clientX, y: e.clientY };
    const base: Frame4 = { x_in: el.frame.x_in, y_in: el.frame.y_in, w_in: el.frame.w_in, h_in: el.frame.h_in };
    let last = base;
    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - from.x) / scale / 96;
      const dy = (ev.clientY - from.y) / scale / 96;
      let f: Frame4;
      if (mode === "move") {
        f = {
          ...base,
          x_in: Math.min(Math.max(snapTo(base.x_in + dx), zx), zx + zw - base.w_in),
          y_in: Math.min(Math.max(snapTo(base.y_in + dy), zy), zy + zh - base.h_in),
        };
      } else {
        f = {
          ...base,
          w_in: Math.max(0.5, Math.min(snapTo(base.w_in + dx), zx + zw - base.x_in)),
          h_in: Math.max(0.3, Math.min(snapTo(base.h_in + dy), zy + zh - base.y_in)),
        };
      }
      last = f;
      setDrag({ id: el.id, frame: f, revision: el.revision });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (last === base) return setDrag(null);
      ui.onFrame?.(el, last);
      window.setTimeout(() => setDrag((d) => (d?.id === el.id && d.frame === last ? null : d)), 2500);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div
      className={`slide-paper${zone ? " free" : ""}`}
      style={{ width: px(w_in), height: px(h_in), background: styler.color(slide.background) ?? "#ffffff" }}
      onDragOver={(e) => {
        if (zone && ui.onDropPicture && e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDrop={(e) => {
        const file = e.dataTransfer.files?.[0];
        if (!zone || !ui.onDropPicture || !file) return;
        e.preventDefault();
        const paper = e.currentTarget as HTMLElement;
        const rect = paper.getBoundingClientRect();
        const scale = rect.width / paper.offsetWidth;
        ui.onDropPicture(slide, file, { x_in: (e.clientX - rect.left) / scale / 96, y_in: (e.clientY - rect.top) / scale / 96 });
      }}
    >
      {slide.shows_master && master.map((el) => <ElementView key={el.id} el={el} styler={styler} page={page} ui={ui} />)}
      {zone && <div className="free-zone" style={{ left: px(zone[0]), top: px(zone[1]), width: px(zone[2]), height: px(zone[3]) }} />}
      {slide.elements.map((el) => (
        <ElementView key={el.id} el={shown(el)} styler={styler} page={page} ui={ui} />
      ))}
      {slide.elements.filter(movable).map((el) => {
        const f = shown(el).frame;
        return (
          <div key={`g${el.id}`} className={`grips${drag?.id === el.id ? " dragging" : ""}`}
               style={{ left: px(f.x_in), top: px(f.y_in), width: px(f.w_in), height: px(f.h_in) }}>
            <span className="grip move" title="Drag to move" onPointerDown={(e) => start(e, el, "move")}>⠿</span>
            <span className="grip resize" title="Drag to resize" onPointerDown={(e) => start(e, el, "resize")} />
            {ui.onEditBox && boundToRecord(el) && (
              <span className="grip edit" title={`Change the ${el.type}: rows, columns, figures`} onPointerDown={(e) => e.stopPropagation()}
                    onClick={() => ui.onEditBox?.(el)}>
                ✎
              </span>
            )}
            {ui.onRemoveBox && (
              <span className="grip remove" title="Remove this box" onPointerDown={(e) => e.stopPropagation()} onClick={() => ui.onRemoveBox?.(el)}>
                ✕
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Scales a slide to the width it is given. */
export function Fit({ width, pageWidthIn, pageHeightIn, children }: { width: number; pageWidthIn: number; pageHeightIn: number; children: ReactNode }) {
  const scale = width / px(pageWidthIn);
  return (
    <div className="fit" style={{ width, height: px(pageHeightIn) * scale }}>
      <div style={{ transform: `scale(${scale})`, transformOrigin: "0 0" }}>{children}</div>
    </div>
  );
}

/** The width of an element, kept up to date; the element may appear later (a callback ref). */
export function useWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    if (!node) return;
    observer.current = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    observer.current.observe(node);
  }, []);
  return [ref, width];
}
