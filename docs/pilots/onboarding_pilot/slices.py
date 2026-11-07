"""Cuts of the step 0 extraction: each step gets only the part it needs. Code only, no firm names."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from onboarding_pilot.common import firm_dir, load_step


def materials(firm: str, kind: str) -> list[dict]:
    return [m for m in load_step(firm, 0)["materials"] if m["kind"] == kind]


def raw(firm: str, material: dict) -> dict:
    return json.loads((firm_dir(firm) / material["extraction"]).read_text(encoding="utf-8"))


def guide_text(firm: str) -> str | None:
    guides = materials(firm, "guide")
    if not guides:
        return None
    return "\n\n".join((firm_dir(firm) / g["text"]).read_text(encoding="utf-8") for g in guides)


def guide_images(firm: str) -> list[Path]:
    return [firm_dir(firm) / p for g in materials(firm, "guide") for p in g["page_images"]]


def design_source(firm: str) -> tuple[dict | None, dict | None]:
    """The PowerPoint that carries the firm's design: the template, or else the first memo
    (a memo carries its own theme, master and layouts)."""
    for kind in ("template", "memo"):
        found = materials(firm, kind)
        if found:
            return found[0], raw(firm, found[0])
    return None, None


def _colour_name(colour: dict | None) -> str | None:
    if not colour:
        return None
    return f"{colour.get('hex')} ({colour['theme']})" if colour.get("theme") else colour.get("hex")


def _walk(elements):
    for element in elements:
        yield element
        yield from _walk(element.get("children", []))


def theme_and_styles(data: dict) -> str:
    """Theme colours and fonts, master default text styles, page size."""
    lines = [f"PAGE {data['page']['width_in']} x {data['page']['height_in']} in",
             "THEME COLOURS " + ", ".join(f"{k}={v}" for k, v in data["theme"]["colors"].items()),
             f"THEME FONTS heading={data['theme']['fonts']['heading']} body={data['theme']['fonts']['body']}"]
    for family, levels in data["master"]["text_styles"].items():
        for level, style in levels.items():
            parts = [f"{k}={_colour_name(v) if isinstance(v, dict) else v}" for k, v in style.items()]
            lines.append(f"MASTER DEFAULT {family} level {level}: " + " ".join(parts))
    return "\n".join(lines)


def style_inventory(data: dict, limit: int = 40) -> str:
    """Every distinct text look the file uses (font, size, bold, caps, colour), with the boxes that use
    it, most used first. Lets the model name text roles from what is really on the slides."""
    looks: dict[tuple, list[str]] = defaultdict(list)

    def add(style: dict, where: str):
        key = (style.get("font"), style.get("size_pt"), bool(style.get("bold")), style.get("caps"),
               _colour_name(style.get("color")))
        if key[1] is not None and where not in looks[key]:
            looks[key].append(where)

    for layout in data["layouts"]:
        for el in _walk(layout["elements"]):
            if el.get("effective_style"):
                add(el["effective_style"], f"{layout['name']} / {el['name']}")
            for p in el.get("paragraphs", []):
                for run in p.get("runs", []):
                    if run.get("size_pt"):
                        add(run, f"{layout['name']} / {el['name']}")
    lines = []
    for key, where in sorted(looks.items(), key=lambda kv: -len(kv[1]))[:limit]:
        font, size, bold, caps, colour = key
        look = " ".join(str(x) for x in (font or "(theme font)", f"{size:g}pt", "bold" if bold else "", "caps" if caps else "", colour or "") if x)
        lines.append(f"{look}  ← {len(where)} boxes, e.g. {'; '.join(where[:4])}")
    return "\n".join(lines)


def off_page(frame: dict | None, page: dict) -> bool:
    """A box outside the visible page (notes and instructions people leave beside a slide)."""
    if not frame:
        return False
    return (frame["x"] >= page["width_in"] or frame["y"] >= page["height_in"]
            or frame["x"] + frame["w"] <= 0 or frame["y"] + frame["h"] <= 0)


def colours_used(data: dict) -> Counter:
    """Every colour the file really uses on the page (fills, lines, text, table cells, chart series),
    by hex. Boxes outside the visible page are not part of the design and are skipped."""
    counts: Counter = Counter()
    page = data["page"]

    def visit(node):
        if isinstance(node, dict):
            if off_page(node.get("frame"), page):
                return
            if isinstance(node.get("hex"), str) and re.fullmatch(r"#[0-9A-F]{6}", node["hex"]):
                counts[node["hex"]] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit({"master": data["master"], "layouts": data["layouts"], "slides": data["slides"]})
    return counts


def layout_index(data: dict) -> str:
    """Every layout: name, the slides that use it, and its boxes (placeholders and fixed text)."""
    used: dict[str, list[int]] = defaultdict(list)
    for slide in data["slides"]:
        used[slide["layout"]].append(slide["number"])
    lines = []
    for layout in data["layouts"]:
        boxes = []
        for el in layout["elements"]:
            if el.get("placeholder"):
                boxes.append(f"{el['name']} ({el['placeholder']['type']})")
            elif el.get("text") and el["kind"] == "text":
                boxes.append(f"fixed text {el['text'][:40]!r}")
        slides = ", ".join(map(str, used.get(layout["name"], []))) or "none"
        lines.append(f"{layout['name']!r} (slides {slides}): " + "; ".join(boxes))
    return "\n".join(lines)


def off_page_notes(data: dict) -> str:
    """Text boxes left outside the visible page, with the layout of their slide. Templates often
    carry their instructions this way."""
    lines = []
    for slide in data["slides"]:
        for el in _walk(slide["elements"]):
            if el.get("text") and off_page(el.get("frame"), data["page"]):
                lines.append(f"slide {slide['number']} ({slide['layout']}): {el['text'].replace(chr(10), ' / ')}")
    return "\n".join(lines)


def outline(data: dict) -> str:
    """The slides in order: number, layout, and the title (or, without a title box, its first text)."""
    lines = []
    for slide in data["slides"]:
        title = next((el.get("text") for el in slide["elements"]
                      if (el.get("placeholder") or {}).get("type") in ("title", "center_title") and el.get("text")), None)
        if not title:
            title = next((el["text"] for el in _walk(slide["elements"])
                          if el.get("text") and not off_page(el.get("frame"), data["page"])), "")
        lines.append(f"{slide['number']:>3}. [{slide['layout']}] {title.replace(chr(10), ' / ')[:110]}")
    return "\n".join(lines)


FIGURE = re.compile(r"[-−(]?[£€$]?\d[\d,]*(?:\.\d+)?\s?(?:%|x|bn|m|k)?\)?")


def number_samples(firm: str, limit: int = 60) -> str:
    """Numbers as the past memos wrote them, with a little context. Used to infer formats when there
    is no guide."""
    seen, lines = set(), []
    for memo in materials(firm, "memo"):
        data = raw(firm, memo)
        for slide in data["slides"]:
            for el in _walk(slide["elements"]):
                texts = [el.get("text") or ""]
                for row in (el.get("table") or {}).get("rows", []):
                    texts += [c.get("text", "") for c in row["cells"]]
                for text in texts:
                    for m in FIGURE.finditer(text):
                        token = m.group().strip()
                        if len(token) < 2 or token in seen or not re.search(r"[£€$%x(,.kmbn−-]", token):
                            continue
                        seen.add(token)
                        start = max(0, m.start() - 30)
                        lines.append(f"{token!r} in “…{text[start:m.end() + 20].strip()}…”")
                        if len(lines) >= limit:
                            return "\n".join(lines)
    return "\n".join(lines)
