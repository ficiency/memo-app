import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
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
  onLeave: (el: El, text: string | null) => void;
  onFigureRemoved: (el: El) => void;
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
// Figures (chips) cannot be typed into; if one is deleted, the box goes back to its last good text and the
// page explains why. Leaving the box (click elsewhere, Escape) saves it through the door.
function Editor({ el, styler, page, ui }: { el: El; styler: Styler; page: number; ui: Interaction }) {
  const ref = useRef<HTMLDivElement>(null);
  const start = useRef(plainText(el.paragraphs));
  const good = useRef<Paragraph[]>(el.paragraphs);
  const [shown, setShown] = useState<{ version: number; paragraphs: Paragraph[] }>({ version: 0, paragraphs: el.paragraphs });
  const chips = el.paragraphs.flatMap((p) => p.segments.filter((s) => s.kind === "chip")).length;

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

  const onInput = () => {
    const text = read();
    const found = ref.current?.querySelectorAll("[data-chip]").length ?? 0;
    const rebuilt = found === chips ? rebuild(text, good.current) : null;
    if (rebuilt === null) {
      setShown((v) => ({ version: v.version + 1, paragraphs: good.current }));
      ui.onFigureRemoved(el);
      return;
    }
    good.current = rebuilt;
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
        ui.onLeave(el, text === start.current ? null : text);
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
              <span key={j} data-chip contentEditable={false} className={`chip${isNoData(s) ? " nodata" : ""}`}>
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
  const shown = (draft !== undefined && rebuild(draft, el.paragraphs)) || (pendingText !== undefined && rebuild(pendingText, el.paragraphs)) || el.paragraphs;
  const editable = !el.master && el.firm_lock !== "fixed";
  const fill = styler.color(el.fill);
  const line = styler.color(el.line);
  const inset = el.placeholder ? { padding: `${px(0.05)}px ${px(0.1)}px` } : {};
  const color = other ? ui.colors.get(other) : undefined;
  return (
    <div
      className={`el text${editable ? " editable" : ""}${mine ? " mine" : ""}${other ? " held" : ""}${pendingText !== undefined ? " saving" : ""}`}
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
    </div>
  );
}

function TableEl({ el, styler, page }: { el: El; styler: Styler; page: number }) {
  const t = el.table!;
  const st = styler.spec.table_style ?? {};
  const role = styler.tableRole();
  const font = role?.font ?? styler.spec.fonts.tables ?? styler.spec.fonts.body;
  const size = ptPx(role?.size_pt ?? 10);
  const cells = new Map(t.cells.map((c) => [`${c.row}|${c.col}`, c]));
  const headerFill = styler.color(st.header_fill_ref as string);
  const headerText = styler.color(st.header_text_ref as string);
  const bodyText = styler.color(st.body_text_ref as string) ?? styler.color(role?.color_ref) ?? "#222";
  const banding = styler.color(st.banding_ref as string);
  const rule = styler.color(st.rule_color_ref as string) ?? "#d0d0d0";
  return (
    <div className="el table" style={box(el)}>
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
                return (
                  <td
                    key={ci}
                    style={{
                      background: fill ?? undefined,
                      color: header ? headerText ?? bodyText : bodyText,
                      fontWeight: (header && st.header_bold) || r.kind === "group" || r.kind === "total" ? 700 : 400,
                      textAlign: ci > 0 && !header && st.number_align ? (st.number_align as CSSProperties["textAlign"]) : undefined,
                      borderBottom: `1px solid ${rule}`,
                    }}
                  >
                    {text.map((segs, i) => (
                      <div key={i}>
                        <Segments segments={segs} page={page} />
                      </div>
                    ))}
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
  if (el.type === "table" && el.table) return <TableEl el={el} styler={styler} page={page} />;
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

export function Slide({
  slide,
  master,
  styler,
  ui,
}: {
  slide: SlideData;
  master: El[];
  styler: Styler;
  ui: Interaction;
}) {
  const page = slide.position + 1;
  const { w_in, h_in } = styler.spec.page;
  return (
    <div
      className="slide-paper"
      style={{ width: px(w_in), height: px(h_in), background: styler.color(slide.background) ?? "#ffffff" }}
    >
      {slide.shows_master && master.map((el) => <ElementView key={el.id} el={el} styler={styler} page={page} ui={ui} />)}
      {slide.elements.map((el) => (
        <ElementView key={el.id} el={el} styler={styler} page={page} ui={ui} />
      ))}
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
