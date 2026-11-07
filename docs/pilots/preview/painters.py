"""Two painters of the same memo: HTML (a stand-in for the canvas) and PowerPoint (the export).
Both read only the resolved memo from elements.py; neither reads the other."""
from __future__ import annotations

import html
from pathlib import Path

PX_PER_IN = 96
CHIP = "#1F6FEB"
MODEL = "#7A7A7A"


def _label(seg: dict) -> str:
    kind, text = seg["kind"], seg.get("text", "")
    if kind == "chip":
        return f"[{text}]"
    if kind == "model":
        return f"‹model: {text[:70]}›"
    if kind == "person":
        return f"‹person: {text[:60]}›"
    if kind == "cond":
        return f"‹if {seg.get('when', '')[:40]}: {text}›"
    return text


# ------------------------------------------------------------------ HTML

def _html_segments(segments: list[dict]) -> str:
    out = []
    for seg in segments:
        text = html.escape(_label(seg)).replace("\n", "<br>")
        kind = seg["kind"]
        if kind == "chip":
            out.append(f'<span class="chip">{text}</span>')
        elif kind in ("model", "cond", "hint"):
            out.append(f'<span class="draft">{text}</span>')
        elif kind == "person":
            out.append(f'<span class="person">{text}</span>')
        else:
            out.append(text)
    return "".join(out)


def to_html(memo: dict, scale: float = 1.0) -> str:
    ppi = PX_PER_IN * scale
    w, h = memo["page"]["width_in"] * ppi, memo["page"]["height_in"] * ppi
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(memo['firm'])} preview</title>
<style>
body {{ background:#E8E8E8; font-family:Arial; margin:24px; }}
.slide {{ position:relative; width:{w:.1f}px; height:{h:.1f}px; background:white; margin:0 auto 28px; overflow:hidden;
          box-shadow:0 1px 4px rgba(0,0,0,.25); }}
.caption {{ width:{w:.1f}px; margin:0 auto 6px; font:12px Arial; color:#555; }}
.el {{ position:absolute; box-sizing:border-box; overflow:hidden; }}
.text {{ display:flex; flex-direction:column; white-space:pre-wrap; line-height:1.15; }}
.chip {{ color:{CHIP}; background:#EAF2FF; border-radius:3px; padding:0 2px; }}
.draft {{ color:{MODEL}; font-style:italic; }}
.person {{ color:#9A6700; border:1px dashed #C9A227; padding:0 2px; }}
table {{ border-collapse:collapse; width:100%; }}
td,th {{ border-bottom:1px solid #D0D0D0; padding:1px 4px; text-align:left; vertical-align:top; }}
th {{ font-weight:bold; }}
</style></head><body>"""]
    for slide in memo["slides"]:
        parts.append(f'<div class="caption">Slide {slide["number"]} · {html.escape(slide["section"])} '
                     f'({slide["status"]}) · layout {html.escape(slide["layout"])}</div><div class="slide">')
        for el in slide["elements"]:
            f = el["frame"]
            box = f"left:{f['x'] * ppi:.1f}px;top:{f['y'] * ppi:.1f}px;width:{max(f['w'], 0.01) * ppi:.1f}px;height:{max(f['h'], 0.01) * ppi:.1f}px;"
            if el["type"] == "line":
                colour = (el.get("line") or {}).get("hex") or "#999999"
                width = max(1, (el.get("line") or {}).get("width_pt", 0.75) * ppi / 72)
                parts.append(f'<div class="el" style="{box}height:0;border-top:{width:.1f}px solid {colour}"></div>')
                continue
            if el["type"] == "image":
                if el.get("src"):
                    parts.append(f'<img class="el" src="{el["src"]}" style="{box}object-fit:contain">')
                continue
            style = box
            if el.get("fill"):
                style += f"background:{el['fill']};"
            if el.get("line") and el["type"] == "shape":
                style += f"border:1px solid {el['line']['hex']};"
            ts = el.get("text_style")
            if ts:
                size = ts["size_pt"] * ppi / 72
                justify = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}.get(ts["anchor"], "flex-start")
                align = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}.get(ts["align"], "left")
                style += (f"font-family:'{ts['font']}';font-size:{size:.1f}px;color:{ts['color']};text-align:{align};"
                          f"justify-content:{justify};" + ("font-weight:bold;" if ts["bold"] else "")
                          + ("text-transform:uppercase;" if ts["caps"] else ""))
            if el.get("table"):
                t = el["table"]
                head = "".join(f"<th>{html.escape(c)}</th>" for c in t["header"])
                rows = "".join("<tr>" + "".join(f"<td>{_html_segments([c])}</td>" for c in row) + "</tr>" for row in t["rows"])
                parts.append(f'<div class="el" style="{style}"><table><tr>{head}</tr>{rows}</table></div>')
            elif el.get("content") is not None:
                parts.append(f'<div class="el text" style="{style}"><div>{_html_segments(el["content"])}</div></div>')
            else:
                parts.append(f'<div class="el" style="{style}"></div>')
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


# ------------------------------------------------------------------ PowerPoint

def to_pptx(memo: dict, out: Path, assets_dir: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    def rgb(hexcode: str | None):
        return RGBColor.from_string(hexcode.lstrip("#")) if hexcode else None

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(memo["page"]["width_in"]), Inches(memo["page"]["height_in"])
    blank = prs.slide_layouts[6]
    for slide_data in memo["slides"]:
        slide = prs.slides.add_slide(blank)
        for el in slide_data["elements"]:
            f = el["frame"]
            x, y, w, h = Inches(f["x"]), Inches(f["y"]), Inches(max(f["w"], 0.01)), Inches(max(f["h"], 0.01))
            if el["type"] == "line":
                shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, y, x + w, y + Inches(f["h"]))
                line = el.get("line") or {}
                if line.get("hex"):
                    shape.line.color.rgb = rgb(line["hex"])
                shape.line.width = Pt(line.get("width_pt") or 0.75)
            elif el["type"] == "image":
                if not el.get("src"):
                    continue
                shape = slide.shapes.add_picture(str(assets_dir.parent / el["src"]), x, y, width=w, height=h)
            elif el.get("table"):
                t = el["table"]
                rows, cols = len(t["rows"]) + 1, len(t["header"])
                shape = slide.shapes.add_table(rows, cols, x, y, w, h)
                ts = el.get("text_style") or {}
                for c, text in enumerate(t["header"]):
                    cell = shape.table.cell(0, c)
                    cell.text = text
                for r, row in enumerate(t["rows"], 1):
                    for c, seg in enumerate(row[:cols]):
                        cell = shape.table.cell(r, c)
                        cell.text = _label(seg)
                for r in range(rows):
                    for c in range(cols):
                        for p in shape.table.cell(r, c).text_frame.paragraphs:
                            for run in p.runs:
                                run.font.size = Pt(ts.get("size_pt", 10))
                                if ts.get("font"):
                                    run.font.name = ts["font"]
            else:
                kind = MSO_SHAPE.RECTANGLE
                shape = slide.shapes.add_shape(kind, x, y, w, h) if el["type"] == "shape" else slide.shapes.add_textbox(x, y, w, h)
                if el.get("fill"):
                    shape.fill.solid()
                    shape.fill.fore_color.rgb = rgb(el["fill"])
                elif el["type"] == "shape":
                    shape.fill.background()
                if el["type"] == "shape":
                    if el.get("line") and el["line"].get("hex"):
                        shape.line.color.rgb = rgb(el["line"]["hex"])
                    else:
                        shape.line.fill.background()
                ts = el.get("text_style")
                if el.get("content") and ts:
                    tf = shape.text_frame
                    tf.word_wrap = True
                    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
                        setattr(tf, m, Emu(0))
                    tf.vertical_anchor = {"middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}.get(ts["anchor"], MSO_ANCHOR.TOP)
                    lines = [[]]
                    for seg in el["content"]:
                        chunks = _label(seg).split("\n")
                        for i, chunk in enumerate(chunks):
                            if i:
                                lines.append([])
                            lines[-1].append((seg["kind"], chunk))
                    for i, line in enumerate(lines):
                        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                        p.alignment = {"ctr": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}.get(ts["align"], PP_ALIGN.LEFT)
                        for kind_, chunk in line:
                            run = p.add_run()
                            run.text = chunk.upper() if ts["caps"] and kind_ == "text" else chunk
                            run.font.size = Pt(ts["size_pt"])
                            run.font.name = ts["font"]
                            run.font.bold = bool(ts["bold"])
                            colour = CHIP if kind_ == "chip" else MODEL if kind_ in ("model", "cond", "hint") else "#9A6700" if kind_ == "person" else ts["color"]
                            run.font.color.rgb = rgb(colour)
                            run.font.italic = kind_ in ("model", "cond", "hint")
            # Identity that survives export and import: which element of which memo this shape is.
            shape.name = f"spec:{el['id']}"
    prs.save(str(out))
