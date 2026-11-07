"""Step 3: layouts. Every layout the structure needs, as a list of boxes (the element model the canvas
and the PowerPoint export share): type, frame, role, style, lock, fixed text.

Two paths, per layout, decided by step 2:
    imported      the layout exists in the design file. Code copies its boxes exactly (position, style,
                  fixed text); the model only names the role of each box.
    to_generate   no design file has it. The model designs its boxes from the guide (text + images),
                  using the page, palette and text roles approved in step 1.
Locks are set by code, not by the model: fixed shapes and fixed text are locked by the firm;
placeholders can change content but not move.

Code then compares every imported text box with the style of its role: where the template differs
from a role the guide defines, it is a conflict for the person.

Usage (from docs/pilots):
    python -m onboarding_pilot.step3_layouts <firm> [--review-only]
"""
from __future__ import annotations

import argparse
import re
import sys

from onboarding_pilot import slices
from onboarding_pilot.checks import guide_quote_errors
from onboarding_pilot.common import firm_dir, load_step, save_result, save_step
from onboarding_pilot.llm import call_with_checks

TO_GENERATE = "to_generate"
BOX_TYPES = ("text", "table", "chart", "image", "shape", "line")
LOCKS = ("firm", "content", "free")


# ------------------------------------------------------------------ inputs from earlier steps

def approved(firm: str) -> tuple[dict, dict]:
    return load_step(firm, 1)["answer"], load_step(firm, 2)["answer"]


def needed_layouts(structure: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str, dict]]]:
    """(imported: [(layout, section)], to generate: [(name, section, section dict)])."""
    imported, generate = [], []
    for section in structure.get("sections") or []:
        for layout in section.get("layouts") or []:
            if str(layout).startswith(TO_GENERATE):
                name = layout.split(":", 1)[1] if ":" in layout else f"{section['name']} layout"
                generate.append((name, section["name"], section))
            else:
                imported.append((layout, section["name"]))
    return imported, generate


def palette_by_hex(style: dict) -> dict[str, str]:
    return {str(p.get("hex", "")).upper(): p.get("name") for p in style.get("palette") or []}


# ------------------------------------------------------------------ imported layouts (code)

def _style_of(el: dict) -> dict:
    if el.get("effective_style"):
        return el["effective_style"]
    for p in el.get("paragraphs") or []:
        for run in p.get("runs") or []:
            if run.get("text", "").strip():
                return {k: v for k, v in run.items() if k != "text"}
    return {}


def table_cell_style(data: dict, layout_name: str, idx: int) -> dict:
    """The look of body cells in the tables the file's own slides put in this placeholder: a table
    placeholder's default style is not what its cells use."""
    from collections import Counter
    looks = Counter()
    for slide in data["slides"]:
        if slide["layout"] != layout_name:
            continue
        for el in slide["elements"]:
            if (el.get("placeholder") or {}).get("idx") == idx and el.get("table"):
                for row in el["table"]["rows"][1:]:
                    for cell in row["cells"]:
                        st = cell.get("style") or {}
                        if st.get("size_pt"):
                            looks[(st.get("size_pt"), (st.get("color") or {}).get("hex"))] += 1
    if not looks:
        return {}
    (size, hexcode), _ = looks.most_common(1)[0]
    return {"size_pt": size, "color": {"hex": hexcode} if hexcode else None}


def import_boxes(data: dict, layout: dict, prefix: str) -> list[dict]:
    """The layout's boxes as elements, exactly as the file has them. Boxes outside the page are notes."""
    boxes = []
    for i, el in enumerate(layout["elements"]):
        if slices.off_page(el.get("frame"), data["page"]) or not el.get("frame"):
            continue
        ph = el.get("placeholder")
        # An empty placeholder reads as text; its type says what it will hold.
        kind = {"table": "table", "chart": "chart", "picture": "image"}.get((ph or {}).get("type"), el["kind"])
        kind = kind if kind in BOX_TYPES else "shape"
        box = {"id": f"{prefix}{i}", "name": el["name"], "type": kind, "frame": el["frame"],
               "placeholder": ph, "lock": "content" if ph else "firm", "source": "imported"}
        style = _style_of(el)
        if style:
            box["style"] = {k: style[k] for k in ("font", "size_pt", "bold", "caps", "color", "align", "anchor") if k in style}
        for key in ("fill", "line"):
            if el.get(key):
                box[key] = el[key]
        if el.get("text"):
            box["prompt" if ph else "text"] = el["text"]
        if kind == "table" and ph:
            cells = table_cell_style(data, layout.get("name", ""), ph["idx"])
            if cells:
                box["cell_style"] = cells
        boxes.append(box)
    return boxes


def master_boxes(data: dict) -> list[dict]:
    fake_layout = {"elements": [e for e in data["master"]["elements"] if not e.get("placeholder")]}
    return import_boxes(data, fake_layout, "m")


def boxes_text(boxes: list[dict]) -> str:
    lines = []
    for b in boxes:
        ph = b.get("placeholder")
        kind = f"{b['type']} placeholder {ph['type']}" if ph else f"{b['type']} fixed"
        f = b["frame"]
        style = b.get("style") or {}
        look = " ".join(str(x) for x in (style.get("font"), f"{style['size_pt']:g}pt" if style.get("size_pt") else None,
                                         "bold" if style.get("bold") else None,
                                         (style.get("color") or {}).get("hex") if isinstance(style.get("color"), dict) else None) if x)
        words = b.get("text") or b.get("prompt") or ""
        lines.append(f"  {b['id']} [{kind}] {b['name']!r} @({f['x']:g},{f['y']:g}) {f['w']:g}x{f['h']:g} {look}"
                     + (f" text={words[:70]!r}" if words else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ prompts

ROLE_INSTRUCTIONS = """You name the role of every box in a firm's PowerPoint layouts, so that the firm's text
styles and rules can be applied to them. Each box is listed with its id, kind, name, position, look and text.

Text boxes (text placeholders and fixed text) get a role from the TEXT ROLES list; pick the role whose
description matches what the box holds. When no role in the list fits, do not force one: answer
"new:<snake_case name>" and the person will decide. Other boxes (tables, charts, images, shapes, lines) get a short
snake_case role saying what they are (for example a role for a decorative band, a logo, a rule, a data table).
Use the same role name for boxes that do the same job in different layouts. Tables also get
"text_role": the TEXT ROLE their cells use.

Return JSON only:
{"layouts": [{"name": "<layout name>", "boxes": [{"id": "<box id>", "role": "<role>", "text_role": "<tables only>"}]}],
 "master": [{"id": "<box id>", "role": "<role>"}]}
Every box id listed must appear once in your answer."""

GENERATE_INSTRUCTIONS = """A firm has no PowerPoint file for some of its layouts. Design each of them as a list
of boxes, from its guide (text and page images) and from the house style already approved: page size and
margins, palette, text roles. Positions are in inches on that page; stay inside the margins. Read the guide
from its page images first: they show how the firm draws things; the guide may itself be laid out on the
firm's own master. Give evidence for each layout.

Every box has:
  name; role (for text boxes: one of the TEXT ROLES; otherwise a short snake_case role);
  type (text|table|chart|image|shape|line); frame {"x", "y", "w", "h"} in inches;
  fill and line colours as palette names, when the box has them;
  lock ("firm": part of the master or a locked page, fixed; "content": its text changes, the box does not move;
        "free": the box can be moved);
  text (only for fixed text the firm always writes); what (for the others: what goes in it);
  assumed (true only when the guide says nothing about this box existing; positions estimated from the
           images are not "assumed").

Return JSON only:
{"master": [<boxes shared by every page, when the firm has a master>],
 "layouts": [{"name": "<layout name, exactly as given>", "boxes": [<boxes>], "evidence": [{"source": "guide",
              "quote": "<exact words from the guide>"}]}],
 "open_questions": ["..."]}"""


def role_list(style: dict) -> str:
    return "\n".join(f"  {r.get('role')}: {r.get('applies_to')} ({r.get('font')} {r.get('size_pt')}pt)"
                     for r in style.get("text_roles") or [])


# ------------------------------------------------------------------ validation

def overlaps(a: dict, b: dict) -> bool:
    return not (a["x"] + a["w"] <= b["x"] + 0.01 or b["x"] + b["w"] <= a["x"] + 0.01
                or a["y"] + a["h"] <= b["y"] + 0.01 or b["y"] + b["h"] <= a["y"] + 0.01)


def validate_roles(imported: dict[str, list[dict]], master: list[dict], text_roles: set[str]):
    def validate(answer: dict) -> list[str]:
        errors = []
        given = {l.get("name"): {b.get("id"): b.get("role") for b in l.get("boxes") or []} for l in answer.get("layouts") or []}
        text_role_of = {(l.get("name"), b.get("id")): b.get("text_role") for l in answer.get("layouts") or [] for b in l.get("boxes") or []}
        given_master = {b.get("id"): b.get("role") for b in answer.get("master") or []}
        for layout, boxes in imported.items():
            if layout not in given:
                errors.append(f"layout {layout!r} is missing from the answer")
                continue
            for b in boxes:
                role = given[layout].get(b["id"])
                if not role:
                    errors.append(f"{layout} / {b['id']} {b['name']!r} has no role")
                elif b["type"] == "text" and role not in text_roles and not str(role).startswith("new:"):
                    errors.append(f"{layout} / {b['id']} {b['name']!r}: text role {role!r} is not one of the TEXT ROLES")
                if b["type"] == "table":
                    tr = text_role_of.get((layout, b["id"]))
                    if tr not in text_roles and not str(tr).startswith("new:"):
                        errors.append(f"{layout} / {b['id']} {b['name']!r}: table needs a text_role from the TEXT ROLES, got {tr!r}")
        for b in master:
            if b["id"] not in given_master:
                errors.append(f"master box {b['id']} {b['name']!r} has no role")
        return errors
    return validate


def validate_generated(names: list[str], style: dict, guide: str | None):
    page = style.get("page") or {}
    w, h = page.get("width_in"), page.get("height_in")
    margins = page.get("margins_in") or {}
    palette = {str(p.get("name", "")).lower() for p in style.get("palette") or []}
    roles = {r.get("role"): r for r in style.get("text_roles") or []}

    def validate(answer: dict) -> list[str]:
        errors = []
        got = {l.get("name"): l for l in answer.get("layouts") or []}
        for name in names:
            if name not in got:
                errors.append(f"layout {name!r} is missing from the answer")
        groups = [("master", answer.get("master") or [])] + [(l.get("name"), l.get("boxes") or []) for l in answer.get("layouts") or []]
        for group, boxes in groups:
            for b in boxes:
                where = f"{group} / {b.get('name')}"
                f = b.get("frame") or {}
                if not all(isinstance(f.get(k), (int, float)) for k in ("x", "y", "w", "h")):
                    errors.append(f"{where}: frame needs numeric x, y, w, h")
                    continue
                if f["x"] < -0.01 or f["y"] < -0.01 or f["x"] + f["w"] > w + 0.01 or f["y"] + f["h"] > h + 0.01:
                    errors.append(f"{where}: outside the page")
                elif b.get("type") in ("text", "table", "chart", "image") and (
                        f["x"] < margins.get("left", 0) - 0.02 or f["x"] + f["w"] > w - (margins.get("right") or 0) + 0.02):
                    errors.append(f"{where}: outside the side margins")
                if b.get("type") not in BOX_TYPES:
                    errors.append(f"{where}: type must be one of {BOX_TYPES}")
                if b.get("lock") not in LOCKS:
                    errors.append(f"{where}: lock must be one of {LOCKS}")
                if b.get("type") == "text" and b.get("role") not in roles:
                    errors.append(f"{where}: text role {b.get('role')!r} is not one of the TEXT ROLES")
                for key in ("fill", "line"):
                    if b.get(key) and str(b[key]).lower() not in palette:
                        errors.append(f"{where}: {key} {b[key]!r} is not a palette name")
            content = [b for b in boxes if b.get("type") in ("text", "table", "chart", "image") and b.get("frame")]
            for i, b1 in enumerate(content):
                for b2 in content[i + 1:]:
                    try:
                        if overlaps(b1["frame"], b2["frame"]):
                            errors.append(f"{group}: boxes {b1.get('name')!r} and {b2.get('name')!r} overlap")
                    except (KeyError, TypeError):
                        pass
        errors += guide_quote_errors(answer, guide)
        return errors
    return validate


# ------------------------------------------------------------------ style conflicts (code)

def style_conflicts(layouts: list[dict], style: dict) -> list[dict]:
    """Imported text box vs the style of its role, where that role comes from the guide."""
    roles = {r.get("role"): r for r in style.get("text_roles") or []}
    hexes = {str(p.get("name", "")).lower(): str(p.get("hex", "")).upper() for p in style.get("palette") or []}
    found: dict[tuple, dict] = {}
    for layout in layouts:
        if layout["source"] != "imported":
            continue
        for b in layout["boxes"]:
            role = roles.get(b.get("text_role") if b["type"] == "table" else b.get("role"))
            if b["type"] not in ("text", "table") or not role or (role.get("evidence") or {}).get("source") != "guide":
                continue
            st = b.get("cell_style") if b["type"] == "table" else b.get("style")
            if not st:
                continue
            diffs = []
            lo = role.get("min_size_pt") or role.get("size_pt")
            hi = role.get("max_size_pt") or role.get("size_pt")
            if st.get("size_pt") and lo and hi and not (lo - 0.01 <= st["size_pt"] <= hi + 0.01):
                allowed = f"{lo:g}pt" if lo == hi else f"{lo:g}–{hi:g}pt"
                diffs.append(("size", f"{st['size_pt']:g}pt", allowed))
            box_hex = (st.get("color") or {}).get("hex") if isinstance(st.get("color"), dict) else None
            role_hex = hexes.get(str(role.get("color", "")).lower())
            if box_hex and role_hex and box_hex.upper() != role_hex:
                diffs.append(("colour", box_hex, f"{role_hex} ({role.get('color')})"))
            for what, template_value, guide_value in diffs:
                key = (b["role"], what, template_value)
                entry = found.setdefault(key, {"role": b.get("text_role") if b["type"] == "table" else b["role"], "what": what, "template": template_value,
                                               "guide": guide_value, "boxes": []})
                entry["boxes"].append(f"{layout['name']} / {b['name']}")
    return list(found.values())


# ------------------------------------------------------------------ drawing

def draw(firm: str, result: dict, style: dict) -> None:
    from PIL import Image, ImageDraw

    page = style.get("page") or {}
    scale = 100
    names = {str(p.get("name", "")).lower(): p.get("hex") for p in style.get("palette") or []}
    out = firm_dir(firm) / "step_3_layouts"
    out.mkdir(exist_ok=True)

    def colour(value):
        if isinstance(value, dict):
            return value.get("hex")
        return names.get(str(value or "").lower())

    for layout in result["layouts"]:
        img = Image.new("RGB", (int(page["width_in"] * scale), int(page["height_in"] * scale)), "white")
        d = ImageDraw.Draw(img)
        boxes = (result.get("master") or []) + layout["boxes"] if layout.get("shows_master", True) else layout["boxes"]
        for b in boxes:
            f = b["frame"]
            rect = [f["x"] * scale, f["y"] * scale, (f["x"] + f["w"]) * scale, (f["y"] + max(f["h"], 0.01)) * scale]
            fill = colour(b.get("fill"))
            outline = {"firm": "#E4572E", "content": "#333333", "free": "#2E7D32"}.get(b.get("lock"), "#999999")
            try:
                d.rectangle(rect, outline=outline, fill=fill)
            except ValueError:
                d.rectangle(rect, outline=outline)
            d.text((rect[0] + 2, rect[1] + 1), f"{b.get('role')}"[:38], fill="#FFFFFF" if fill and fill.upper() < "#888888" else "#000000")
        safe = re.sub(r"[^A-Za-z0-9]+", "_", layout["name"]).strip("_")
        img.save(out / f"{safe}.png")


# ------------------------------------------------------------------ run

def run(firm: str, review_only: bool = False) -> None:
    style, structure = approved(firm)
    imported_needed, generate_needed = needed_layouts(structure)
    if review_only:
        result = load_step(firm, 3)
    else:
        material, data = slices.design_source(firm)
        layouts, attempts, unresolved, master = [], [], [], []
        if imported_needed and data:
            by_name = {l["name"]: l for l in data["layouts"]}
            imported: dict[str, list[dict]] = {}
            parts = [f"TEXT ROLES\n{role_list(style)}"]
            master = master_boxes(data)
            parts.append(f"=== MASTER (on every slide whose layout shows it)\n{boxes_text(master)}")
            for n, (name, section) in enumerate(dict.fromkeys(imported_needed)):
                layout = by_name[name]
                imported[name] = import_boxes(data, layout, f"L{n}b")
                parts.append(f"=== LAYOUT {name!r} (section {section!r})\n{boxes_text(imported[name])}")
            call = call_with_checks(ROLE_INSTRUCTIONS, "\n\n".join(parts), [],
                                    validate_roles(imported, master, {r.get("role") for r in style.get("text_roles") or []}))
            attempts += [dict(a, call="roles") for a in call["attempts"]]
            unresolved += call["unresolved"]
            roles = {l.get("name"): {b.get("id"): b.get("role") for b in l.get("boxes") or []} for l in call["answer"].get("layouts") or []}
            text_roles_of = {(l.get("name"), b.get("id")): b.get("text_role") for l in call["answer"].get("layouts") or [] for b in l.get("boxes") or []}
            master_roles = {b.get("id"): b.get("role") for b in call["answer"].get("master") or []}
            for b in master:
                b["role"] = master_roles.get(b["id"])
            for name, section in dict.fromkeys(imported_needed):
                boxes = imported[name]
                for b in boxes:
                    b["role"] = roles.get(name, {}).get(b["id"])
                    if b["type"] == "table":
                        b["text_role"] = text_roles_of.get((name, b["id"]))
                layouts.append({"name": name, "section": section, "source": "imported",
                                "shows_master": by_name[name]["shows_master_elements"], "boxes": boxes})
        generated_answer = {}
        if generate_needed:
            names = [n for n, _, _ in generate_needed]
            page = style.get("page") or {}
            parts = [f"PAGE {page.get('width_in')} x {page.get('height_in')} in, margins {page.get('margins_in')}",
                     "PALETTE " + ", ".join(f"{p.get('name')} {p.get('hex')} ({p.get('use')})" for p in style.get("palette") or []),
                     f"TEXT ROLES\n{role_list(style)}",
                     "LAYOUTS TO DESIGN\n" + "\n".join(f"  {n!r} for section {s!r} ({sec.get('status')}): must contain {sec.get('must_contain')}"
                                                       for n, s, sec in generate_needed)]
            guide = slices.guide_text(firm)
            if guide:
                parts.append(f"=== GUIDE (text; page images follow)\n{guide}")
            call = call_with_checks(GENERATE_INSTRUCTIONS, "\n\n".join(parts), slices.guide_images(firm),
                                    validate_generated(names, style, guide))
            attempts += [dict(a, call="generate") for a in call["attempts"]]
            unresolved += call["unresolved"]
            generated_answer = call["answer"]
            sections = {n: s for n, s, _ in generate_needed}
            for i, b in enumerate(generated_answer.get("master") or []):
                master.append(dict(b, id=f"gm{i}", source="generated"))
            for n, layout in enumerate(generated_answer.get("layouts") or []):
                boxes = [dict(b, id=f"G{n}b{i}", source="generated") for i, b in enumerate(layout.get("boxes") or [])]
                layouts.append({"name": layout.get("name"), "section": sections.get(layout.get("name")), "source": "generated",
                                "shows_master": True, "boxes": boxes, "evidence": layout.get("evidence")})
        result = {"master": master, "layouts": layouts, "attempts": attempts, "unresolved": unresolved,
                  "open_questions": generated_answer.get("open_questions") or []}
        save_result(firm, 3, result)
    result["conflicts"] = style_conflicts(result["layouts"], style)
    draw(firm, result, style)
    path = save_step(firm, 3, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def review(firm: str, result: dict) -> str:
    out = [f"STEP 3 · Layouts for {firm}", "",
           f"Drawings: out/onboarding/{firm}/step_3_layouts/ (orange = locked by the firm, dark = content only, green = free)", ""]
    for layout in result["layouts"]:
        roles = [b.get("role") or "?" for b in layout["boxes"]]
        out.append(f"  {layout['source']:<9} {layout['name']!r} ({layout.get('section')}): {len(roles)} boxes")
        out.append(f"            roles: {', '.join(roles)}")
    out.append(f"  master: {', '.join(b.get('role') or '?' for b in result.get('master') or [])}")
    proposed = {}
    for layout in result["layouts"]:
        for b in layout["boxes"]:
            if str(b.get("role")).startswith("new:"):
                proposed.setdefault(b["role"][4:], []).append(f"{layout['name']} / {b['name']}")
    out += ["", f"NEW TEXT ROLES PROPOSED ({len(proposed)}, for the person: add to the house style or pick an existing role)"]
    out += [f"  + {role}: {len(where)} boxes, e.g. {', '.join(where[:3])}" for role, where in proposed.items()]
    out += ["", f"TEMPLATE VS GUIDE STYLE ({len(result.get('conflicts') or [])} conflicts for the person)"]
    for c in result.get("conflicts") or []:
        out.append(f"  ? {c['role']} {c['what']}: template {c['template']} vs guide {c['guide']} — {len(c['boxes'])} boxes, e.g. {', '.join(c['boxes'][:3])}")
    if result.get("open_questions"):
        out += ["", "OPEN QUESTIONS"] + [f"  ? {q}" for q in result["open_questions"]]
    if result.get("unresolved"):
        out += ["", "CHECKS STILL FAILING (for the person)"] + [f"  ! {e}" for e in result["unresolved"]]
    att = result.get("attempts") or []
    out += ["", f"Model calls: {len(att)}, seconds {sum(x['seconds'] for x in att):.0f}, "
                f"tokens in {sum(x['prompt_tokens'] or 0 for x in att)}, out {sum(x['completion_tokens'] or 0 for x in att)}"]
    for x in att:
        out.append(f"  {x['call']}: {len(x['errors'])} failed checks" + (f" ({x['errors'][0][:100]}…)" if x["errors"] else ""))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    parser.add_argument("--review-only", action="store_true")
    args = parser.parse_args()
    run(args.firm, args.review_only)


if __name__ == "__main__":
    main()
