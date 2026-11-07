"""Compact, readable view of a PowerPoint read by pptx_reader, grouped by layout.

The full reader output describes the layouts and the slides separately. A layout is a mould
(where each box sits and how it looks); a slide fills that mould. This view puts them together:
for every layout, each box once, with its position, its style and what the slides put in it.

It is generic: it never looks for names or wording. It only uses what every .pptx has:
which slides use which layout, which boxes come from the layout (placeholders), which boxes
were added on the slide, and whether a box sits outside the visible page.

Usage:
    python -m app.readers.pptx_view <reader-output.json> [--out <file.txt>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def color_name(color) -> str | None:
    if not color:
        return None
    name = color.get("theme") or color.get("hex")
    mods = color.get("mods")
    return f"{name}({','.join(f'{k}={v}' for k, v in mods.items())})" if mods else name


def style_text(style: dict | None) -> str:
    """One line: 'Georgia 22pt bold dk2 align=l anchor=middle autofit=shrink'."""
    if not style:
        return ""
    parts = []
    for key in ("font",):
        if style.get(key):
            parts.append(str(style[key]))
    if style.get("size_pt"):
        parts.append(f"{style['size_pt']:g}pt")
    for flag in ("bold", "italic", "underline"):
        if style.get(flag):
            parts.append(flag)
    if style.get("caps"):
        parts.append("caps")
    if style.get("color"):
        parts.append(color_name(style["color"]))
    if style.get("align") and style["align"] != "l":
        parts.append(f"align={style['align']}")
    if style.get("anchor") and style["anchor"] != "top":
        parts.append(f"anchor={style['anchor']}")
    if style.get("autofit") and style["autofit"] != "none":
        parts.append(f"autofit={style['autofit']}")
    if style.get("bullet"):
        bullet_color = color_name(style.get("bullet_color"))
        parts.append(f"bullet={style['bullet']}" + (f"({bullet_color})" if bullet_color else ""))
    return " ".join(parts)


def frame_text(frame: dict | None) -> str:
    if not frame:
        return "@inherited"
    return f"@({frame['x']:g},{frame['y']:g}) {frame['w']:g}x{frame['h']:g}in"


def is_off_page(frame: dict | None, page: dict) -> bool:
    if not frame:
        return False
    return (frame["x"] >= page["width_in"] or frame["y"] >= page["height_in"]
            or frame["x"] + frame["w"] <= 0 or frame["y"] + frame["h"] <= 0)


def overrides(paragraphs: list[dict]) -> list[str]:
    """Runs whose style is set by hand on the slide (sizes, colours, fonts typed over the default)."""
    seen = []
    for p in paragraphs or []:
        for run in p.get("runs", []):
            style = style_text({k: v for k, v in run.items() if k not in ("text", "field")})
            if style and run["text"].strip():
                entry = f'"{run["text"].strip()[:40]}" {style}'
                if entry not in seen:
                    seen.append(entry)
    return seen


def cell_look(cell: dict) -> str:
    """Fill, text style and alignment of one cell, as a short tag."""
    parts = []
    if cell.get("fill"):
        parts.append(f"fill={color_name(cell['fill'])}")
    style = style_text(cell.get("style"))
    if style:
        parts.append(style)
    if cell.get("align") and cell["align"] != "l":
        parts.append(f"align={cell['align']}")
    return " ".join(parts)


def table_text(table: dict) -> str:
    """Rows as text. A look shared by a whole row is written once for the row; a cell that
    differs from its row (a RAG status, a breach in red) carries its own tag."""
    rows = table["rows"]
    lines = [f"TABLE {len(rows)}x{len(table['column_widths_in'])}"]
    for n, row in enumerate(rows, 1):
        cells = [c for c in row["cells"] if not c.get("spanned")]
        looks = [cell_look(c) for c in cells]
        # most common look; ties go to the first one seen (a set here made the choice change between runs)
        shared = max(dict.fromkeys(looks), key=looks.count) if looks else ""
        parts = []
        for cell, look in zip(cells, looks):
            text = cell.get("text", "").replace("\n", " / ")
            parts.append(f"{text}{{{look}}}" if look != shared else text)
        lines.append(f"r{n}" + (f" [{shared}]" if shared else "") + ": " + " | ".join(parts))
    return " || ".join(lines)


def content_text(element: dict) -> str:
    """What a box holds, as short text."""
    kind = element["kind"]
    if kind == "table":
        return table_text(element["table"])
    if kind == "chart":
        chart = element["chart"]
        out = [f"CHART {chart['type']}"]
        for plot in chart["plots"]:
            out.append(f"categories={plot['categories']}")
            for s in plot["series"]:
                colors = f" color={color_name(s.get('color'))}" if s.get("color") else ""
                points = ""
                if s.get("point_colors"):
                    points = " points={" + ", ".join(f"{i}:{color_name(c)}" for i, c in s["point_colors"].items()) + "}"
                out.append(f"series {s['name']!r}={s['values']}{colors}{points}")
        return " ".join(out)
    if kind == "image":
        image = element.get("image", {})
        return f"IMAGE asset={image.get('asset')}" + (f" alt={image['alt']!r}" if image.get("alt") else "")
    if kind == "group":
        return "GROUP: " + " ; ".join(f"{c['kind']} {c['name']!r} {content_text(c)}" for c in element.get("children", []))
    return (element.get("text") or "").replace("\n", " / ")


def fixed_look(element: dict) -> str:
    parts = []
    if element.get("fill"):
        parts.append(f"fill={color_name(element['fill'])}")
    line = element.get("line")
    if line:
        parts.append(f"line={color_name(line.get('color'))}" + (f"/{line['width_pt']:g}pt" if line.get("width_pt") else ""))
    if element.get("geometry"):
        parts.append(f"shape={element['geometry']}")
    return " ".join(parts)


def build_view(data: dict) -> str:
    page = data["page"]
    out = [f"FILE {data['file']}",
           f"PAGE {page['width_in']:g} x {page['height_in']:g} in",
           "PALETTE " + " ".join(f"{k}={v}" for k, v in data["theme"]["colors"].items()),
           f"FONTS heading={data['theme']['fonts']['heading']} body={data['theme']['fonts']['body']}",
           ""]

    master = data["master"]
    out.append("MASTER (on every slide whose layout shows master elements)")
    for family, levels in master["text_styles"].items():
        for level, style in levels.items():
            out.append(f"  default {family} level {level}: {style_text(style)}")
    for el in master["elements"]:
        if el.get("placeholder"):
            continue
        out.append(f"  fixed {el['kind']} {el['name']!r} {frame_text(el['frame'])} {fixed_look(el)} {content_text(el)}".rstrip())
    out.append("")

    slides_by_layout: dict[str, list[dict]] = {}
    for slide in data["slides"]:
        slides_by_layout.setdefault(slide["layout"], []).append(slide)

    for layout in data["layouts"]:
        slides = slides_by_layout.get(layout["name"], [])
        used = ", ".join(str(s["number"]) for s in slides) or "none"
        out.append(f"LAYOUT {layout['name']!r}  (slides: {used})")
        if not layout["shows_master_elements"]:
            out.append("  hides master elements")
        if layout.get("background"):
            out.append(f"  background={color_name(layout['background'])}")

        for el in layout["elements"]:
            ph = el.get("placeholder")
            if not ph:
                text = content_text(el)
                out.append(f"  fixed {el['kind']} {el['name']!r} {frame_text(el['frame'])} {fixed_look(el)}"
                           + (f" text={text!r}" if text else "") + (f"  [{style_text(el['paragraphs'][0]['runs'][0])}]"
                                                                    if el.get("paragraphs") and el['paragraphs'][0].get("runs") else ""))
                continue
            out.append(f"  box [{ph['type']} #{ph['idx']}] {el['name']!r} {frame_text(el['frame'])}  {style_text(el.get('effective_style'))}")
            prompt = content_text(el)
            if prompt:
                out.append(f"      layout prompt: {prompt!r}")
            for slide in slides:
                match = next((s for s in slide["elements"] if (s.get("placeholder") or {}).get("idx") == ph["idx"]), None)
                if match is None:
                    out.append(f"      slide {slide['number']}: (box deleted)")
                    continue
                # A table grows with its rows, so only its position and width count as a move.
                keys = ("x", "y", "w") if match["kind"] == "table" else ("x", "y", "w", "h")
                moved = bool(match.get("frame") and el.get("frame")
                             and any(match["frame"].get(k) != el["frame"].get(k) for k in keys))
                out.append(f"      slide {slide['number']}: {content_text(match)!r}" + (f"  moved to {frame_text(match['frame'])}" if moved else ""))
                for o in overrides(match.get("paragraphs")):
                    out.append(f"         set by hand: {o}")

        for slide in slides:
            for el in slide["elements"]:
                if el.get("placeholder"):
                    continue
                where = "OFF PAGE " if is_off_page(el.get("frame"), page) else ""
                out.append(f"  slide {slide['number']} added {where}{el['kind']} {el['name']!r} {frame_text(el.get('frame'))} {fixed_look(el)}".rstrip())
                text = content_text(el)
                if text:
                    out.append(f"      {text!r}")
                for o in overrides(el.get("paragraphs")):
                    out.append(f"         set by hand: {o}")
        out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("json", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    data = json.loads(args.json.read_text(encoding="utf-8"))
    view = build_view(data)
    target = args.out or args.json.with_suffix(".view.txt")
    target.write_text(view, encoding="utf-8")
    sys.stdout.write(f"{target}  chars={len(view)} ~tokens={len(view) // 4}\n")


if __name__ == "__main__":
    main()
