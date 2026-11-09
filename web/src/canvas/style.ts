import type { CSSProperties } from "react";
import type { El, Paragraph, Seg, SpecView } from "../api/types";

// The canvas paints from the memo's data and the spec's styles: a paragraph's own style, else its role's,
// else the firm's body font. Palette names become hex; inches and points become pixels (96 per inch).

export const DPI = 96;
export const px = (inches: number | null | undefined) => (inches ?? 0) * DPI;
export const ptPx = (pt: number) => (pt * DPI) / 72;

const SERIF = /georgia|times|garamond|serif|cambria|baskerville|palatino/i;

export function fontStack(name: string | null | undefined): string {
  if (!name) return "Arial, Helvetica, sans-serif";
  const generic = SERIF.test(name) ? "Georgia, 'Times New Roman', serif" : "Arial, Helvetica, sans-serif";
  const extra = /narrow/i.test(name) ? ", 'Arial Narrow', Arial" : "";
  return `'${name}'${extra}, ${generic}`;
}

export type Resolved = {
  font: string;
  size: number;
  bold: boolean;
  italic: boolean;
  caps: boolean;
  color: string;
  align: string;
  anchor: string;
  lineHeight: number;
  before: number;
  after: number;
  bullet: string | null;
  bulletColor: string | null;
  indent: number;
};

export class Styler {
  private palette: Map<string, string>;
  constructor(public spec: SpecView) {
    this.palette = new Map(Object.entries(spec.palette).map(([k, v]) => [k.toLowerCase(), v]));
  }

  color(ref: string | null | undefined): string | null {
    if (!ref) return null;
    if (ref.startsWith("#")) return ref;
    return this.palette.get(ref.toLowerCase()) ?? null;
  }

  role(name: string | null | undefined) {
    return name ? this.spec.roles[name] : undefined;
  }

  /** The role of tables (the painter uses the first role named for tables). */
  tableRole() {
    const name = Object.keys(this.spec.roles).find((r) => r.includes("table"));
    return name ? this.spec.roles[name] : undefined;
  }

  paragraph(p: Paragraph, el: El): Resolved {
    const s = p.style;
    const r = this.role(p.role) ?? this.role(el.role);
    const body = this.spec.fonts.body ?? this.spec.fonts.heading ?? null;
    return {
      font: fontStack(s.font ?? r?.font ?? body),
      size: s.size_pt ?? r?.size_pt ?? 12,
      bold: s.bold ?? r?.bold ?? false,
      italic: s.italic ?? r?.italic ?? false,
      caps: s.caps ?? r?.caps ?? false,
      color: this.color(s.color_ref) ?? this.color(r?.color_ref) ?? "#222222",
      align: s.align ?? "left",
      anchor: s.anchor ?? "top",
      lineHeight: s.line_spacing && s.line_spacing < 3 ? s.line_spacing * 1.2 : 1.2,
      before: s.space_before_pt ?? 0,
      after: s.space_after_pt ?? 0,
      bullet: s.bullet_char,
      bulletColor: this.color(s.bullet_color_ref),
      indent: s.indent_in ?? 0,
    };
  }

  paragraphCss(p: Paragraph, el: El): CSSProperties {
    const r = this.paragraph(p, el);
    return {
      fontFamily: r.font,
      fontSize: ptPx(r.size),
      fontWeight: r.bold ? 700 : 400,
      fontStyle: r.italic ? "italic" : "normal",
      textTransform: r.caps ? "uppercase" : undefined,
      color: r.color,
      textAlign: r.align as CSSProperties["textAlign"],
      lineHeight: r.lineHeight,
      marginTop: ptPx(r.before),
      marginBottom: ptPx(r.after),
      // indent only where the memo says so (a bullet or an indent): a level alone carries no indent here
      paddingLeft: r.bullet ? px(r.indent || 0.18) + px(0.25 * (p.level || 0)) : px(r.indent),
      ["--bullet-color" as string]: r.bulletColor ?? r.color,
    };
  }
}

export const isSlot = (s: Seg) => s.kind === "model_draft" || s.kind === "person_slot" || s.kind === "conditional";
export const isNoData = (s: Seg) => s.kind === "chip" && s.text.startsWith("[no data");

/** Plain text of paragraphs, one line each, as the door reads it. */
export function plainText(paragraphs: Paragraph[]): string {
  return paragraphs.map((p) => p.segments.map((s) => s.text).join("")).join("\n");
}

/** Paragraphs rebuilt from plain text, with the figures (chips) found again in order: the way the door
 *  rebuilds a box, so what the screen shows while saving is what will be stored. Null if a figure is gone. */
export function rebuild(text: string, from: Paragraph[], removed?: Set<string>, skipMissing = false): Paragraph[] | null {
  const chips = from.flatMap((p) => p.segments.filter((s) => s.kind === "chip" && !(s.id && removed?.has(s.id))));
  const lines = text.split("\n");
  const placed: { line: number; at: number; chip: Seg }[] = [];
  let line = 0;
  let offset = 0;
  for (const chip of chips) {
    let found = -1;
    while (line < lines.length) {
      found = chip.text ? lines[line].indexOf(chip.text, offset) : -1;
      if (found >= 0) break;
      line += 1;
      offset = 0;
    }
    if (found < 0) {
      if (skipMissing) {
        line = 0;
        offset = 0;
        continue; // shown while saving: a figure the edit removes is simply not there
      }
      return null;
    }
    placed.push({ line, at: found, chip });
    offset = found + chip.text.length;
  }
  return lines.map((t, i) => {
    const src = from[Math.min(i, from.length - 1)];
    const segments: Seg[] = [];
    let cursor = 0;
    for (const { at, chip } of placed.filter((x) => x.line === i)) {
      if (at > cursor) segments.push({ kind: "text", text: t.slice(cursor, at), concept: null, path: null });
      segments.push(chip);
      cursor = at + chip.text.length;
    }
    if (cursor < t.length || segments.length === 0) segments.push({ kind: "text", text: t.slice(cursor), concept: null, path: null });
    return { level: src?.level ?? 0, role: src?.role ?? null, style: src?.style ?? ({} as Paragraph["style"]), segments };
  });
}
