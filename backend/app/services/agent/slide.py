"""The memo's agent: a person asks for an edit of one slide in words; the agent proposes it; the person confirms.

Simple and bounded on purpose. One request works on one slide (or one box of it). Code builds everything the
model sees, and nothing else: the slide's boxes (their text, with the figures in them written as {{concept}}),
what the firm's spec says goes in each box and on this section, the firm's rules, and the figures the deal
record has for this firm (each concept with its value as the firm writes it). The model answers with a reply
and edits in JSON. Code turns them into door operations and checks each one with the door without applying
it; anything wrong (a figure typed instead of asked for by name, a box it may not edit, a figure typed over)
goes back to the model to fix, at most twice. What passes becomes a proposal the person applies or discards;
the agent never applies anything. Nothing here knows any firm.
"""
from __future__ import annotations

import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import AgentRun, ChatMessage, ChatThread
from app.db.memo import Element
from app.db.record import RecordRevision
from app.db.session import SessionLocal
from app.services import boxes as record_boxes
from app.services import proposals
from app.services.figures import Figures
from app.services.memo_data import MemoData
from app.services.onboarding.llm import call_with_checks
from app.services.operations.door import MARKER, Door

MAX_EDITS = 12
# an amount typed as text: a currency sign before a number, or a number with %, x, k, m or bn after it
# a table typed as text: two lines or more with columns split by | or tabs
TABLE_LINE = re.compile(r"(?:[^|\n]*\|){2,}|\t.*\t")
TYPED_FIGURE = re.compile(r"[£€$]\s?\d|\d(?:[\d,.]*\d)?\s?(?:%|x\b|k\b|m\b|bn\b)", re.I)

INSTRUCTIONS = """You help a person edit one slide of an investment memo, following the firm's own spec.

You get the slide's boxes (id, what the box is, whether you may edit it, its text now), what the firm's spec
puts in each box and on this section, the firm's rules, and the FIGURES the deal record has for this memo:
each with its name and its value as the firm writes it.

Figures are never typed. Write a figure as {{name}} with a name from FIGURES; the system writes its value. For
a value FIGURES lacks, use one of RECORD FIELDS by its path: {{field:…}}. A figure already in a box appears as
{{name}} or {{path}}: keep it as it is where it should stay; leave it out to remove it.
Never write an amount yourself (no £, €, $, %, x, m, bn, k with a number), and never a figure that is not in
FIGURES or RECORD FIELDS. If the person asks to change a figure's value, make no edit and say that values change in the deal
record.

Text in ‹model: …› marks what the firm expects the model to write there: replace it with that text. Text in
‹person: …› is for a person: leave it unless asked. Edit only boxes marked editable, only what was asked, in
the firm's style and in the memo's language; keep each box to what its spec says it holds, short enough for it.

The memo's slides and the firm's sections (with their layouts and how many slides each allows) are listed too.
You may add a slide of a section on one of its layouts, duplicate, move or delete a slide, and move or resize a
box (inches, on the page given): the firm's rules decide what is allowed (a locked section does not move, a
section allowed once is not duplicated, a box the template places does not move). A slide you add comes with
its layout's boxes (LAYOUT BOXES): write them in the same edit with "texts": {"<role>": "<text>"}. Prefer a
layout with a title and write the title there, in the firm's style, rather than a blank layout with a text box.

On a slide the firm leaves free (its FREE AREA is given) you may add boxes inside that area: text, a table of
the deal record (one of RECORD TABLES, with the rows and columns you choose) or a chart of record figures with
periods (from CHART FIGURES, at most 4, with how many periods). Their values come from the record: you never
type them. A table is always a table you add from RECORD TABLES, never text laid out in columns (no | or tab
separated lines): when the record has no table for it, use a chart of CHART FIGURES or short text instead.
Add them on the slide you work on (SLIDE) unless the person names another slide, or on a slide you
add in the same answer: "on": "new". Lay boxes out so they do not
overlap, and keep each one inside the free area.

Return JSON only:
{"reply": "<one or two sentences to the person: what you propose, or why you cannot>",
 "edits": [{"box": "<box id>", "text": "<the box's whole new text; one line per paragraph>"},
           {"box": "<table box id>", "row": <row>, "col": <col>, "text": "<the cell's new text>"},
           {"box": "<box id>", "frame": {"x_in": <x>, "y_in": <y>, "w_in": <w>, "h_in": <h>}},
           {"slide": "add", "section": "<section name>", "layout": "<layout name>", "position": <slide number it takes>,
            "texts": {"<box role>": "<its text>"}},
           {"slide": "duplicate" | "delete", "id": "<slide id>"},
           {"slide": "move", "id": "<slide id>", "position": <slide number it takes>},
           {"add": "text", "on": "<slide id> | new", "text": "<text; figures as {{name}}>", "frame": {...}},
           {"add": "table", "on": "<slide id> | new", "table": "<record table>", "rows": [...], "columns": [...], "frame": {...}},
           {"add": "chart", "on": "<slide id> | new", "series": ["<chart figure>"], "chart_type": "column" | "bar" | "line",
            "periods": <n>, "names": {"<chart figure>": "<series name>"}, "frame": {...}}]}
Return "edits": [] when nothing should change."""


def _typed(text: str) -> str | None:
    """What is wrong with text the agent writes: amounts typed instead of figures, or a table laid out as text."""
    plain = MARKER.sub("", text)
    typed = [m.group(0) for m in TYPED_FIGURE.finditer(plain)]
    if typed:
        return f"amounts are never typed ({', '.join(typed[:3])}); use {{{{name}}}} from FIGURES or RECORD FIELDS, or leave it out"
    if sum(1 for line in text.split("\n") if TABLE_LINE.search(line)) >= 2:
        return ("a table is never text laid out in columns: add a table from RECORD TABLES, or a chart of CHART FIGURES, "
                "or write short text")
    return None


def _box_text(paragraphs) -> str:
    out = []
    for _, segs in paragraphs:
        line = ""
        for x in segs:
            key = x.concept_name or x.record_path
            line += f"{{{{{key}}}}}" if x.kind == "chip" and key and not x.text.startswith("[no data") \
                else ("" if x.kind == "page_number" else x.text or "")
        out.append(line)
    return "\n".join(out)


def context(session: Session, memo_id, slide_id: str | None, element_id: str | None) -> dict:
    d = MemoData(session, memo_id)
    memo = d.memo
    slide = next((x for x in d.slides if str(x.id) == str(slide_id)), d.slides[0])
    firm = session.get(S.Firm, memo.firm_id)
    revision = session.get(RecordRevision, memo.record_revision_id)
    figures = Figures(session, firm, d.version, revision)
    q = session.scalars
    section = session.get(S.Section, slide.section_id) if slide.section_id else None
    recipe = {}
    origins = [e.origin_element_id for e in d.elements_of.get(slide.id, []) if e.origin_element_id]
    concepts = {c.id: c.name for c in figures.concepts.values()}
    writable = set()                                 # boxes whose recipe has something to write (not only fixed words or memo data)
    for r in q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_(origins)).order_by(S.RecipeSegment.paragraph, S.RecipeSegment.position)):
        if r.kind not in ("fixed", "memo_data"):
            writable.add(r.element_id)
        part = {"fixed": f'"{r.text}"', "figure": f"figure {concepts.get(r.concept_id)}", "fact": f"fact {concepts.get(r.concept_id)}",
                "memo_data": f"memo data {concepts.get(r.concept_id)}", "model_writes": f"model writes: {r.what}",
                "person_writes": f"person writes: {r.what}", "conditional": f"if {r.when_rule}: {r.text}",
                "table": "a table", "chart": "a chart"}.get(r.kind, r.kind)
        recipe.setdefault(r.element_id, []).append(part)
    boxes = []
    for e in sorted(d.elements_of.get(slide.id, []), key=lambda e: (e.y_in or 0, e.x_in or 0)):
        if e.type not in ("text", "table", "chart"):
            continue
        editable = e.firm_lock != "fixed" and (element_id is None or str(e.id) == str(element_id))             and (e.origin_element_id not in recipe or e.origin_element_id in writable)
        box = {"id": str(e.id), "what": e.name or e.role, "role": e.role, "editable": editable, "lock": e.firm_lock,
               "frame": f"x {e.x_in:.2f}, y {e.y_in:.2f}, w {e.w_in:.2f}, h {e.h_in:.2f}",
               "spec": "; ".join(recipe.get(e.origin_element_id, [])) or e.description}
        if e.type == "chart":
            ch = d.chart_of.get(e.id)
            box["editable"] = False                          # its values are the record's; it moves, it is not written
            box["chart"] = "" if ch is None else (f"{ch.chart_type} chart · series: " + ", ".join(
                f"{x.name} ({x.record_path or x.concept_name})" for x in sorted(d.series_of.get(ch.id, []), key=lambda x: x.position)))
        elif e.type == "table" and e.id in d.table_of:
            t = d.table_of[e.id]
            own = t.table_template_id is None                # a person's table: its words theirs, its data the record's

            def cell_writable(c) -> bool:
                if any(x.kind == "chip" for _, ss in d.paragraphs(cell_id=c.id) for x in ss):
                    return False
                return c.kind != "data" if own else c.kind != "fixed"

            box["table"] = [{"row": c.row, "col": c.col, "text": _box_text(d.paragraphs(cell_id=c.id)),
                             "editable": editable and cell_writable(c)}
                            for c in sorted(d.cells_of.get(t.id, []), key=lambda c: (c.row, c.col))]
        else:
            box["text"] = _box_text(d.paragraphs(element_id=e.id))
        boxes.append(box)
    available = []
    for name, b in figures.bindings.items():
        if b.kind == "missing" or name not in figures.concept_named:
            continue
        try:
            chip = figures.chip(name)
        except ValueError:
            continue
        concept = figures.concept_named[name]
        available.append({"name": name, "value": chip["text"], "about": (concept.description or "")[:120]})
    # where people's own boxes go: each layout's free area; and what the record offers for tables and charts
    zones = {z.layout_id: (z.x_in, z.y_in, z.w_in, z.h_in) for z in q(select(S.LayoutZone).where(
        S.LayoutZone.kind == "free", S.LayoutZone.layout_id.in_(list(d.layout_rows))))}
    sec_rows = {x.id: x for x in q(select(S.Section).where(S.Section.spec_version_id == d.version.id))}
    is_free = lambda section_id, layout_id: layout_id in zones and (
        (sec_rows.get(section_id) is not None and sec_rows[section_id].status == "free") or
        (d.layout_rows.get(layout_id) is not None and d.layout_rows[layout_id].is_blank))
    free_sections = any(x.status == "free" for x in sec_rows.values())
    fields = figures.fields()
    rules = [r.says for r in q(select(S.Rule).where(S.Rule.spec_version_id == d.version.id,
                                                   (S.Rule.section_id.is_(None)) | (S.Rule.section_id == slide.section_id)))]
    # the memo's slides and the firm's sections with their layouts, for slide edits
    sections = {x.id: x for x in q(select(S.Section).where(S.Section.spec_version_id == d.version.id).order_by(S.Section.position))}
    layouts_of: dict = {}
    for link in q(select(S.SectionLayout).where(S.SectionLayout.section_id.in_(list(sections)))):
        if link.layout_id in d.layout_rows:
            layouts_of.setdefault(link.section_id, []).append(d.layout_rows[link.layout_id].name)
    used = {l for ls in layouts_of.values() for l in ls}
    layout_ids = [l.id for l in d.layout_rows.values() if l.name in used]
    layout_boxes: dict = {}
    if layout_ids:
        els = q(select(Element).where(Element.layout_id.in_(layout_ids), Element.type == "text")).all()
        what = {}
        for r in q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_([e.id for e in els]),
                                                 S.RecipeSegment.kind.in_(("model_writes", "person_writes")))):
            what.setdefault(r.element_id, r.what)
        for e in els:
            if e.firm_lock != "fixed" and e.role and (e.id in what or e.role in ("title", "subtitle", "body", "content")):
                layout_boxes.setdefault(d.layout_rows[e.layout_id].name, []).append(
                    f"{e.role}" + (f" ({(what.get(e.id) or '')[:70]})" if what.get(e.id) else ""))
    slides = [{"id": str(x.id), "number": x.position + 1, "section": sections[x.section_id].name if x.section_id in sections else "—",
               "layout": d.layout_rows[x.layout_id].name if x.layout_id in d.layout_rows else "—"} for x in d.slides]
    return {"memo": memo, "slide": slide, "boxes": boxes, "figures": available, "rules": rules,
            "section": section, "spec_name": session.get(S.Spec, d.version.spec_id).name, "slides": slides,
            "sections": [{"id": str(x.id), "name": x.name, "status": x.status,
                          "count": x.count if x.count != "n" else f"at most {x.count_n}", "layouts": layouts_of.get(x.id, []),
                          "layout_ids": {d.layout_rows[l].name: str(l) for l in d.layout_rows if d.layout_rows[l].name in layouts_of.get(x.id, [])}}
                         for x in sections.values()],
            "page": f"{d.version.page_w_in} x {d.version.page_h_in} in",
            "free_area": zones.get(slide.layout_id) if is_free(slide.section_id, slide.layout_id) else None,
            "zones": zones,
            "record_tables": record_boxes.record_tables(figures) if free_sections else {},
            "chart_figures": record_boxes.period_families(figures) if free_sections else {},
            "fields": fields,
            "layout_boxes": layout_boxes}


def package(ctx: dict, request: str, target: str | None) -> str:
    section = ctx["section"]
    lines = [f"MEMO: {ctx['memo'].title} (the firm's {ctx['spec_name']})",
             f"SLIDE {ctx['slide'].position + 1}: section {section.name if section else '—'}"
             + (f"; the firm says it must contain: {section.must_contain}" if section and section.must_contain else ""),
             "", "BOXES"]
    for b in ctx["boxes"]:
        lines.append(f"- box {b['id']} · {b['what']} · {'editable' if b['editable'] else 'not editable'} · placed by "
                     f"{'the template' if b['lock'] != 'free' else 'the person'} at {b['frame']} · spec: {b['spec'] or '—'}")
        if "chart" in b:
            lines.append(f"    {b['chart']}")
        elif "table" in b:
            for c in b["table"]:
                lines.append(f"    cell row {c['row']} col {c['col']}{' (editable)' if c['editable'] else ''}: {c['text']}")
        else:
            lines.append("    text now:\n" + "\n".join(f"      {x}" for x in (b["text"] or "(empty)").split("\n")))
    area = ctx["free_area"]
    lines += ["", "FREE AREA: " + (f"x {area[0]:.2f}, y {area[1]:.2f}, w {area[2]:.2f}, h {area[3]:.2f} in: boxes may be added here"
                                   if area else "none: this slide takes no new boxes")]
    lines += ["", f"THE MEMO'S SLIDES (page {ctx['page']})"]
    lines += [f"- slide {x['number']} · id {x['id']} · {x['section']} · {x['layout']}" for x in ctx["slides"]]
    lines += ["", "THE FIRM'S SECTIONS (status · how many slides · layouts)"]
    lines += [f"- {x['name']} · {x['status']} · {x['count']} · {', '.join(x['layouts']) or '—'}" for x in ctx["sections"]]
    lines += ["", "LAYOUT BOXES (what a slide added on each layout has to write: role (what goes there))"]
    lines += [f"- {name}: {'; '.join(boxes)}" for name, boxes in ctx["layout_boxes"].items()]
    if ctx["record_tables"]:
        lines += ["", "RECORD TABLES (for a table you add: name · its rows · its columns)"]
        lines += [f"- {n} · rows: {', '.join(t['rows'][:20])}{' …' if len(t['rows']) > 20 else ''} · columns: {', '.join(t['columns'])}"
                  for n, t in ctx["record_tables"].items()]
        lines += ["", "CHART FIGURES (for a chart you add: name · what it is)"]
        lines += [f"- {n} · {label}" for n, label in ctx["chart_figures"].items()]
    lines += ["", "FIGURES (name = value as the firm writes it · what it is)"]
    lines += [f"- {f['name']} = {f['value']} · {f['about']}" for f in ctx["figures"]]
    lines += ["", "RECORD FIELDS (for what FIGURES lacks: {{path}} = value as the firm writes it · what it is)"]
    lines += [f"- {f['path']} = {f['value']} · {f['label']}" for f in ctx["fields"]]
    lines += ["", "FIRM RULES"] + [f"- {r}" for r in ctx["rules"][:40]]
    lines += ["", f"THE PERSON ASKS{' (about box ' + target + ')' if target else ''}: {request}"]
    return "\n".join(lines)


def to_operations(session: Session, memo_id, ctx: dict, edits: list) -> tuple[list[dict], list[str]]:
    """The model's edits as door operations, and what is wrong with them (checked by the door, not applied)."""
    errors, ops, earlier = [], [], []                 # earlier: the structure changes before, for the checks
    boxes = {b["id"]: b for b in ctx["boxes"]}
    if len(edits) > MAX_EDITS:
        errors.append(f"at most {MAX_EDITS} edits per request")
    for i, e in enumerate(edits[:MAX_EDITS]):
        if e.get("add"):
            op, error = add_operation(ctx, memo_id, e, earlier)
            if error:
                errors.append(f"edit {i + 1} (add {e.get('add')}): {error}")
            elif op:
                ops.append(op)
                earlier.append(op)
            continue
        if e.get("slide"):
            op, error = slide_operation(ctx, memo_id, e, earlier)
            if error:
                errors.append(f"edit {i + 1} (slide {e.get('slide')}): {error}")
            elif op:
                ops.append(op)
                earlier.append(op)
            continue
        box = boxes.get(str(e.get("box")))
        where = f"edit {i + 1} (box {e.get('box')})"
        if box is not None and e.get("frame"):
            f = e["frame"]
            try:
                frame = {k: float(f[k]) for k in ("x_in", "y_in", "w_in", "h_in") if k in f}
            except (TypeError, ValueError):
                errors.append(f"{where}: a frame is four numbers in inches")
                continue
            op = {"type": "set_frame", "target": box["id"], "payload": frame, "label": f"move {box['what']}"}
            with SessionLocal() as s:
                c = Door(s, memo_id).check(op, actor_type="agent")
            if c.status != "applied":
                errors.append(f"{where}: {c.reason}")
            else:
                ops.append(op)
            continue
        if box is None:
            errors.append(f"{where}: no such box on this slide")
            continue
        text = str(e.get("text") or "")
        problem = _typed(text)
        if problem:
            errors.append(f"{where}: {problem}")
            continue
        if "row" in e or "col" in e:
            cell = next((c for c in box.get("table", []) if c["row"] == e.get("row") and c["col"] == e.get("col")), None)
            if cell is None or not cell["editable"]:
                errors.append(f"{where}: cell row {e.get('row')} col {e.get('col')} cannot be edited")
                continue
            if MARKER.search(text):
                errors.append(f"{where}: table cells take words only; figures in tables come from the record")
                continue
            op = {"type": "set_cell", "target": box["id"], "payload": {"row": e["row"], "col": e["col"], "text": text}}
        else:
            if not box["editable"] or "table" in box:
                errors.append(f"{where}: this box cannot be edited")
                continue
            op = keep_or_remove(session, box["id"], text)
        with SessionLocal() as s:
            c = Door(s, memo_id).check(op, actor_type="agent")
        if c.status != "applied":
            errors.append(f"{where}: {c.reason}")
            continue
        ops.append(op)
    return ops, errors


def add_operation(ctx: dict, memo_id, e: dict, earlier: list) -> tuple[dict | None, str | None]:
    """A box the model adds (text, a record table, a record chart) as a door operation, checked by the door after
    the earlier changes of the answer (it may go on the slide the answer adds): (operation, error)."""
    kind = str(e.get("add"))
    if kind not in ("text", "table", "chart"):
        return None, "a box you add is text, a table or a chart"
    on = str(e.get("on") or ctx["slide"].id)
    if on == "new":
        new = next((x for x in reversed(earlier) if x["type"] == "add_slide"), None)
        if new is None:
            return None, '"on": "new" needs a slide added before it in the same answer'
        slide_id, where = new["payload"]["id"], "the new slide"
    else:
        known = {x["id"]: x for x in ctx["slides"]}
        if on not in known:
            return None, "no such slide in this memo"
        slide_id, where = on, f"slide {known[on]['number']}"
    payload: dict = {"slide_id": slide_id, "type": kind}
    f = e.get("frame") or {}
    try:
        payload.update({k: float(f[k]) for k in ("x_in", "y_in", "w_in", "h_in") if k in f})
    except (TypeError, ValueError):
        return None, "a frame is four numbers in inches"
    if kind == "text":
        text = str(e.get("text") or "")
        problem = _typed(text)
        if problem:
            return None, problem
        payload["text"] = text
        label = f"add a text box on {where}"
    elif kind == "table":
        payload.update({"table": e.get("table"), "rows": e.get("rows"), "columns": e.get("columns")})
        label = f"add a table of {str(e.get('table') or '').split(':')[-1].replace('_', ' ')} on {where}"
    else:
        payload.update({"chart_type": e.get("chart_type") or "column", "series": list(e.get("series") or []),
                        "periods": int(e.get("periods") or 6), "names": e.get("names") or {}})
        label = f"add a {payload['chart_type']} chart on {where}"
    op = {"type": "add_element", "target": slide_id, "payload": payload, "label": label}
    with SessionLocal() as s:
        c = Door(s, memo_id).check_after(earlier, op, actor_type="agent")
    return (op, None) if c.status == "applied" else (None, c.reason)


def slide_operation(ctx: dict, memo_id, e: dict, earlier: list | None = None) -> tuple[dict | None, str | None]:
    """A slide edit from the model as a door operation, checked by the door (not applied): (operation, error)."""
    kind = e.get("slide")
    slides = {x["id"]: x for x in ctx["slides"]}
    if kind == "add":
        sec = next((x for x in ctx["sections"] if x["name"].lower() == str(e.get("section", "")).lower()), None)
        if sec is None:
            return None, f"no section {e.get('section')!r} in the firm's spec"
        layout_id = sec["layout_ids"].get(str(e.get("layout") or (sec["layouts"][0] if sec["layouts"] else "")))
        if layout_id is None:
            return None, f"the firm's layouts for {sec['name']!r} are {', '.join(sec['layouts']) or 'none'}"
        position = int(e.get("position") or len(slides) + 1) - 1
        texts = {str(k): str(v) for k, v in (e.get("texts") or {}).items()}
        for role, text in texts.items():
            problem = _typed(text)
            if problem:
                return None, f"the {role} box: {problem}"
        op = {"type": "add_slide", "target": str(memo_id), "label": f"add a {sec['name']} slide ({e.get('layout') or sec['layouts'][0]}) as slide {position + 1}",
              "payload": {"id": str(uuid.uuid4()), "section_id": sec["id"], "layout_id": layout_id, "position": position,
                          **({"texts": texts} if texts else {})}}
    elif kind in ("duplicate", "delete", "move"):
        target = slides.get(str(e.get("id")))
        if target is None:
            return None, "no such slide in this memo"
        op = {"type": f"{kind}_slide", "target": target["id"], "label": f"{kind} slide {target['number']} ({target['section']})"}
        if kind == "move":
            op["payload"] = {"position": int(e.get("position") or target["number"]) - 1}
            op["label"] += f" to {op['payload']['position'] + 1}"
    else:
        return None, f"a slide edit is add, duplicate, move or delete, not {kind!r}"
    with SessionLocal() as s:
        c = Door(s, memo_id).check_after(earlier or [], op, actor_type="agent")
    return (op, None) if c.status == "applied" else (None, c.reason)


def keep_or_remove(session: Session, element_id: str, text: str) -> dict:
    """A box's new text from the model, as a door operation: a figure already in the box and kept as {{name}}
    stays the same chip (its text in place of the marker), a new {{name}} is asked for, one left out is removed."""
    el = session.get(Element, uuid.UUID(element_id))
    d = MemoData(session, _memo_of(session, el), element_ids=[el.id])
    chips = [x for _, ss in d.paragraphs(element_id=el.id) for x in ss if x.kind == "chip" and (x.concept_name or x.record_path)]
    waiting: dict = {}
    for c in chips:
        waiting.setdefault(c.concept_name or c.record_path, []).append(c)
    kept = set()

    def keep(m):
        queue = waiting.get(m.group(1).strip())
        if queue:
            c = queue.pop(0)
            kept.add(c.id)
            return c.text
        return m.group(0)                                    # a new figure: asked for by name

    text = MARKER.sub(keep, text)
    payload = {"text": text}
    removed = [str(c.id) for c in chips if c.id not in kept]
    if removed:
        payload["remove_chips"] = removed
    return {"type": "set_text", "target": element_id, "base_revision": el.revision, "payload": payload}


def _memo_of(session: Session, el: Element):
    from app.db.memo import Slide
    return session.get(Slide, el.slide_id).memo_id


def ask(memo_id, user: S.User, request: str, slide_id: str | None, element_id: str | None) -> dict:
    """One request: the agent's reply and, when it proposes edits, the proposal (nothing is applied)."""
    started = time.time()
    with SessionLocal() as session:
        ctx = context(session, memo_id, slide_id, element_id)
        text = package(ctx, request, element_id)
        tries = []

        def validate(answer: dict) -> list[str]:
            edits = answer.get("edits") or []
            if not isinstance(edits, list):
                return ["edits must be a list"]
            ops, errors = to_operations(session, memo_id, ctx, edits)
            tries.append(ops)
            return errors

        result = call_with_checks(INSTRUCTIONS, text, [], validate)
        ops = tries[-1] if tries else []
        reply = str(result["answer"].get("reply") or "")
        if result["unresolved"]:
            reply += f" ({len(result['unresolved'])} edit(s) left out: {'; '.join(result['unresolved'][:3])})"
        thread = session.scalar(select(ChatThread).where(ChatThread.memo_id == ctx["memo"].id)) or ChatThread(memo_id=ctx["memo"].id, created_by=user.id)
        session.add(thread)
        session.flush()
        session.add(ChatMessage(thread_id=thread.id, author_type="person", author_id=user.id, text=request))
        answer = ChatMessage(id=uuid.uuid4(), thread_id=thread.id, author_type="agent", text=reply)
        session.add(answer)
        session.flush()
        proposal = None
        if ops:
            proposal = proposals.propose(session, memo_id, ops, reply, actor_type="agent", on_behalf_of=user.id, message_id=answer.id)
            answer.change_set_id = uuid.UUID(proposal["id"])
        att = result["attempts"]
        session.add(AgentRun(message_id=answer.id, model=result["model"], attempts=len(att),
                             input_tokens=sum(a["prompt_tokens"] or 0 for a in att), output_tokens=sum(a["completion_tokens"] or 0 for a in att),
                             seconds=round(time.time() - started, 1),
                             validation_errors="\n".join(e for a in att for e in a["errors"]) or None))
        session.commit()
        return {"reply": reply, "proposal": proposal, "attempts": len(att), "seconds": round(time.time() - started, 1),
                "tokens": [sum(a["prompt_tokens"] or 0 for a in att), sum(a["completion_tokens"] or 0 for a in att)]}


def history(session: Session, memo_id) -> list[dict]:
    thread = session.scalar(select(ChatThread).where(ChatThread.memo_id == uuid.UUID(str(memo_id))))
    if thread is None:
        return []
    return [{"author": m.author_type, "text": m.text, "proposal_id": str(m.change_set_id) if m.change_set_id else None,
             "at": m.created_at.isoformat()}
            for m in session.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.created_at))]
