"""Step 4: recipe. For every content box of every layout, what goes in it, as segments:
    fixed          words the firm writes the same way every time
    figure         a number about the deal (named concept)
    fact           a non-numeric fact about the deal (named concept)
    memo_data      set when a memo is created (stage, date, team)
    conditional    words added only when a fact about the deal holds
    model_writes   drafted per deal (may list concepts it must include)
    person_writes  only a person can supply it
Tables are described as structure.

Input package, built box by box by code:
    each layout from step 3: its boxes (id, role, type, the template's prompt text)
    for each past memo slide on that layout: the text of every box, matched to the layout box by its
        placeholder number (exact); boxes people added on the slide are sent apart as unmatched
    step 2: the section each layout serves and what it must contain
    guide: full text + page images
With no past memos (a guide-only firm) the same call works from the guide alone: boxes stay empty,
with their kind of fill, concepts and a description; no phrases are invented.

Code checks: every content box has a recipe; every quoted example exists in that memo's box; "seen in
N memos" is recounted by code; conditionals say when. After the answer, code lists concepts that take
different values inside one memo (possible accidents in that memo) and builds the concept catalogue.

Usage (from backend/):
    python -m app.services.onboarding.step4_recipe <firm> [--review-only]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from app.services.onboarding import slices
from app.services.onboarding.checks import guide_quote_errors
from app.services.onboarding.common import load_step, save_result, save_step
from app.services.onboarding.llm import call_with_checks

SEGMENTS = ("fixed", "figure", "fact", "memo_data", "conditional", "model_writes", "person_writes", "table", "chart")
CHART_TYPES = ("column", "stacked_column", "bar", "stacked_bar", "line", "area", "pie", "scatter", "combo", "other")
PARA = "\u00b6"                                  # paragraph break marker; never part of the text
MASTER = "Master"                                # the master's boxes get recipes like a layout's
CONCEPT = re.compile(r"^[a-z][a-z0-9_]*$")

INSTRUCTIONS = """You write the recipe of a firm's investment memo: for every content box of every layout,
what goes in it, so that a new memo can be generated for a new deal. Some materials may be missing; the
list is at the top.

For each layout you get its section, what the section must contain, and its boxes (id, role, type, the
template's prompt text). When there are past memos, you also get, box by box, what each memo wrote in that
box, plus anything people added on the slide outside the layout's boxes ("unmatched").

Give each box a pattern, a list of segments:
Box texts show paragraph breaks as ¶. A break is not text: never write ¶ or a "/" that stands for a break.
Every segment carries "paragraph": <0 for the first paragraph of the box, 1 for the second...>; a box whose
paragraphs have different looks (listed with the box) gets segments for each paragraph.
- {"type": "fixed", "text": "...", "seen_in": <memos with this exact text>, "confidence": "high|low"}
  Words the firm writes the same way every time. Before calling text fixed, ask: "for a deal with
  different numbers and a different outcome, would this text still be true?" If not, it is model_writes
  or conditional. A box the guide or template describes as a conclusion is written per deal.
- {"type": "figure", "concept": "<snake_case>", "kind": "money|percent|multiple|count|date|period|ratio|other",
   "examples": {"<memo file>": "<exactly as written in that box>"}}  A number about the deal.
- {"type": "fact", "concept": "<snake_case>", "examples": {...}}  A non-numeric fact about the deal
  (company name, round, instrument, adviser).
- {"type": "memo_data", "concept": "<snake_case>", "examples": {...}}  Set when the memo is created:
  stage, memo or meeting date, deal team.
- {"type": "conditional", "text": "...", "when": "<the fact about the deal that decides it>"}
- {"type": "model_writes", "what": "...", "must_include": ["<concepts it must mention>"],
   "style_examples": {"<memo file>": "<what that memo wrote here, exactly>"}}
  Drafted per deal. style_examples keep the firm's way of writing so the draft can follow it.
- {"type": "person_writes", "what": "..."}
- {"type": "table", "header": ["..."], "header_type": "fixed|periods|mixed",
   "columns": "<what the columns are>", "rows": [{"label": "...", "label_type": "fixed|fact|model_writes",
   "cells": "figure|fact|model_writes|person_writes|mixed", "concept": "<snake_case>", "kind": "...",
   "example": {"<memo file>": "<ONE cell of this row, exactly as written>"}}],
   "row_count": "<fixed number|varies>", "notes": "...",
   "column_rule": {"actual_periods": <n>, "forecast_periods": <n>, "label_format": "<how period headers read>"}}
  Rows are the table's real rows: a fixed label must be a label written in the materials (the template's
  table shown with the box, a past memo or the guide), never a category describing several possible rows;
  when the rows change from deal to deal, say so with label_type model_writes. column_rule is required when
  the header holds periods (header_type periods or mixed), null otherwise.
- {"type": "chart", "chart_type": "<column|stacked_column|bar|stacked_bar|line|area|pie|scatter|combo|other>",
   "categories": "periods|items", "category_count": <n or null>,
   "column_rule": {"actual_periods": <n>, "forecast_periods": <n>, "label_format": "..."} or null,
   "series": [{"concept": "<snake_case>", "name": "<series label>", "forecast_distinct": <true/false>}]}
  Every chart box gets exactly one chart segment: what a native chart is built from. When past memos have a
  chart in that box, use their chart type.
- Use the same concept name for the same meaning in every box and layout. Every concept named in
  must_include must be defined somewhere (a figure, fact or memo_data segment, a table row, a chart series)
  or listed in extra_concepts.
- The MASTER layout holds boxes on every page (footer, page number): give them recipes too, from the footer
  the house style describes; page furniture is fixed text or memo_data.
- Period concepts are relative ("prior_actual_year_revenue"), never a year copied from an old memo.
- Page furniture (page numbers, footer labels) is fixed text or memo_data, never a figure.
- Without past memos: do not invent phrases. Use fixed text only where the guide writes the words
  itself; otherwise describe the box with figure, fact, memo_data, model_writes or person_writes.
- Quote examples character for character as written in that memo's box.

If you notice the same figure (same meaning) written with different values in different boxes or slides
of one memo, report it under accidents; this is a hint for the person, not a check.

Unmatched content: say for each item whether it belongs to a box of the layout, is a deal-specific addition,
or looks like an accident.

Return JSON only:
{"recipes": [{"layout": "<layout name>", "box_id": "<box id>", "pattern": [<segments>], "notes": "..."}],
 "extra_concepts": [{"concept": "<snake_case>", "type": "figure|fact|memo_data", "kind": "<money|percent|...|other>"}],
 "unmatched": [{"memo": "...", "slide": <n>, "item": "<name>", "decision": "belongs_to:<box id>|deal_specific|accident", "why": "..."}],
 "accidents": ["<anything in the past memos that looks like a one-off mistake, and why>"],
 "questions": ["<what the materials leave open for the person>"]}"""


# ------------------------------------------------------------------ package

def content_boxes(layout: dict) -> list[dict]:
    """Boxes that need a recipe: text, tables and charts that are not fixed words of the firm."""
    return [b for b in layout["boxes"] if b.get("type") in ("text", "table", "chart") and not b.get("text")]


def element_text(el: dict) -> str:
    if el.get("table"):
        return " || ".join(" | ".join(c.get("text", "").replace("\n", f" {PARA} ") for c in row["cells"] if not c.get("spanned"))
                           for row in el["table"]["rows"])
    if el.get("chart"):
        out = [f"CHART {el['chart']['type']}"]
        for plot in el["chart"]["plots"]:
            out.append(f"categories {plot['categories']}")
            out += [f"series {s['name']!r} {s['values']}" for s in plot["series"]]
        return " ".join(out)
    return (el.get("text") or "").replace("\n", f" {PARA} ")


def memo_slides(firm: str) -> dict[str, list[dict]]:
    """For every memo: its slides (from the raw read)."""
    return {m["file"]: slices.raw(firm, m)["slides"] for m in slices.materials(firm, "memo")}


def match_slide(slide: dict, page: dict) -> tuple[dict[int, str], list[tuple[str, str]]]:
    """Placeholder number -> text, and the boxes people added (name, text)."""
    matched, unmatched = {}, []
    for el in slide["elements"]:
        if slices.off_page(el.get("frame"), page):
            continue
        ph = el.get("placeholder")
        text = element_text(el)
        if ph:
            matched[ph["idx"]] = text
        elif text.strip() or el["kind"] in ("table", "chart", "image"):
            unmatched.append((el["name"], text if text.strip() else f"[{el['kind']}]"))
    return matched, unmatched


def looks_text(b: dict) -> str:
    """The paragraph looks step 3 kept for a box (G1)."""
    looks = b.get("paragraphs") or b.get("levels") or []
    if len(looks) < 2:
        return ""
    return " paragraphs: " + " | ".join(f"{i}: {p.get('size_pt') or '?'}pt{' bold' if p.get('bold') else ''}"
                                        + (f" {p['text'][:30]!r}" if p.get("text") else "") for i, p in enumerate(looks))


def table_text(b: dict) -> str:
    """The template's own table for a box (G4 from step 3): header, row kinds and first-column labels."""
    t = b.get("table")
    if not t:
        return ""
    lines = [f"     template table: {len(t.get('column_widths_in') or [])} columns"]
    for r in t["rows"]:
        cells = [c["text"].replace("\n", f" {PARA} ") for c in r["cells"] if not c.get("spanned")]
        lines.append(f"       {r['kind']}: {' | '.join(cells)[:160]}")
    return "\n".join(lines)


def table_texts(b: dict) -> str:
    t = b.get("table") or {}
    return " ".join(c.get("text", "") for r in t.get("rows") or [] for c in r["cells"])


def chart_type_of(el: dict) -> str | None:
    t = str((el.get("chart") or {}).get("type") or "").upper()
    if not t:
        return None
    stacked = "STACKED" in t
    for key, name in (("COLUMN", "column"), ("BAR", "bar"), ("LINE", "line"), ("AREA", "area"), ("PIE", "pie"),
                      ("DOUGHNUT", "pie"), ("SCATTER", "scatter"), ("XY", "scatter")):
        if key in t:
            return f"stacked_{name}" if stacked and name in ("column", "bar") else name
    return "other"


def periods_of(header: list[str]) -> tuple[int, int]:
    """(actual, forecast) period columns in a table header: FY24A is actual; FY25F, 2026E, 2027B forecast."""
    actual = forecast = 0
    for h in header:
        h = str(h).strip()
        if not PERIOD.match(h):
            continue
        if h[-1:].upper() in ("F", "E", "B", "P"):
            forecast += 1
        else:
            actual += 1
    return actual, forecast


def build_package(firm: str):
    step0 = load_step(firm, 0)
    step3 = load_step(firm, 3)
    structure, layouts = load_step(firm, 2)["answer"], list(step3["layouts"])
    if any(b.get("type") == "text" and not b.get("text") for b in step3.get("master") or []):
        layouts.append({"name": MASTER, "section": None, "boxes": step3.get("master") or []})
    sections = {s["name"]: s for s in structure.get("sections") or []}
    memos = memo_slides(firm)
    _, design = slices.design_source(firm)
    page = design["page"] if design else {"width_in": 1e9, "height_in": 1e9}
    present = sorted({m["kind"] for m in step0["materials"]})
    parts = [f"MATERIALS SENT: {', '.join(present)}. MISSING: {', '.join(step0['missing']) or 'nothing'}."]
    footer = (load_step(firm, 1)["answer"].get("footer") or {})
    parts.append(f"=== FOOTER in the house style: {' | '.join(footer.get('items') or [])}; "
                 f"slide number: {footer.get('slide_number')}")
    template_tables: dict[tuple, str] = {}
    template_labels: dict[tuple, list[str]] = {}
    chart_types: dict[tuple, set] = defaultdict(set)
    chart_series: dict[tuple, dict[str, set]] = defaultdict(lambda: defaultdict(set))   # (layout, box) -> memo -> names
    period_split: dict[tuple, dict[str, tuple]] = defaultdict(dict)    # (layout, box) -> source -> (actual, forecast)
    box_texts: dict[tuple, dict[str, str]] = defaultdict(dict)   # (layout, box id) -> memo -> text
    slide_texts: dict[tuple, dict[str, str]] = defaultdict(dict)  # (layout) -> memo -> all text on its slides
    required: dict[str, list[str]] = {}
    for layout in layouts:
        boxes = content_boxes(layout)
        required[layout["name"]] = [b["id"] for b in boxes]
        section = sections.get(layout.get("section"), {})
        if layout["name"] == MASTER:
            lines = [f"=== LAYOUT {MASTER!r}: boxes on every page that shows the master"]
        else:
            lines = [f"=== LAYOUT {layout['name']!r} · section {layout.get('section')!r} ({section.get('status')}): "
                     f"must contain {section.get('must_contain')}"]
        for b in layout["boxes"]:
            if b.get("text"):
                lines.append(f"  fixed {b['type']} {b.get('role')}: {b['text'][:80]!r}")
        by_idx = {(b.get("placeholder") or {}).get("idx"): b for b in boxes if b.get("placeholder")}
        for b in boxes:
            what = b.get("prompt") or b.get("what") or ""
            lines.append(f"  BOX {b['id']} {b['type']} role={b.get('role')}" + (f" prompt={what[:120]!r}" if what else "")
                         + looks_text(b))
            if b.get("table"):
                split = periods_of([c.get("text", "") for c in b["table"]["rows"][0]["cells"]])
                if split != (0, 0):
                    period_split[(layout["name"], b["id"])]["template"] = split
                lines.append(table_text(b))
                template_tables[(layout["name"], b["id"])] = table_texts(b)
                template_labels[(layout["name"], b["id"])] = [r["cells"][0]["text"] for r in b["table"]["rows"]
                                                              if r["kind"] == "data" and r["cells"]]
        for memo, slides_ in memos.items():
            for slide in slides_:
                if slide["layout"] != layout["name"]:
                    continue
                matched, unmatched = match_slide(slide, page)
                for el in slide["elements"]:
                    box = by_idx.get((el.get("placeholder") or {}).get("idx"))
                    if box and chart_type_of(el):
                        chart_types[(layout["name"], box["id"])].add(chart_type_of(el))
                        for plot in el["chart"].get("plots") or []:
                            chart_series[(layout["name"], box["id"])][memo] |= {norm(x.get("name") or "").lower()
                                                                          for x in plot.get("series") or [] if x.get("name")}
                    if box and el.get("table"):
                        split = periods_of([c.get("text", "") for c in el["table"]["rows"][0]["cells"]])
                        if split != (0, 0):
                            period_split[(layout["name"], box["id"])][memo] = split
                lines.append(f"  -- {memo} slide {slide['number']}")
                for idx, text in matched.items():
                    box = by_idx.get(idx)
                    if box:
                        box_texts[(layout["name"], box["id"])][memo] = text
                        lines.append(f"     {box['id']}: {text!r}")
                for name, text in unmatched:
                    lines.append(f"     unmatched {name!r}: {text!r}")
                slide_texts[layout["name"]][memo] = " ".join(list(matched.values()) + [t for _, t in unmatched])
        parts.append("\n".join(lines))
    guide = slices.guide_text(firm)
    if guide:
        parts.insert(1, f"=== GUIDE (text; page images follow)\n{guide}")
    box_types = {(l["name"], b["id"]): b["type"] for l in layouts for b in content_boxes(l)}
    context = {"guide": guide, "required": required, "box_texts": box_texts, "slide_texts": slide_texts,
               "memos": list(memos), "template_tables": template_tables, "template_labels": template_labels,
               "chart_types": chart_types, "chart_series": chart_series, "period_split": period_split,
               "box_types": box_types}
    return "\n\n".join(parts), slices.guide_images(firm), context


# ------------------------------------------------------------------ checks

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("’", "'").replace("−", "-").replace(PARA, " ")).strip()


def stem(name: str) -> str:
    return re.sub(r"(\.view\.txt|\.json)$", "", name)


def examples_of(seg: dict) -> dict:
    if seg.get("type") == "table":
        return {}
    if seg.get("type") == "model_writes":
        return seg.get("style_examples") or {}
    return seg.get("examples") or {}


def make_validate(context: dict):
    memos = {stem(m) for m in context["memos"]}

    def find(layout, box_id, memo, example) -> bool:
        memo = stem(memo)
        text = context["box_texts"].get((layout, box_id), {}).get(memo) or context["slide_texts"].get(layout, {}).get(memo, "")
        return norm(example) in norm(text)

    def validate(answer: dict) -> list[str]:
        errors = []
        got = {(r.get("layout"), r.get("box_id")): r for r in answer.get("recipes") or []}
        for layout, ids in context["required"].items():
            for box_id in ids:
                if (layout, box_id) not in got:
                    errors.append(f"{layout} / {box_id}: no recipe")
        for (layout, box_id), r in got.items():
            if layout not in context["required"] or box_id not in context["required"][layout]:
                errors.append(f"{layout} / {box_id}: not a content box of that layout")
                continue
            for seg in r.get("pattern") or []:
                kind = seg.get("type")
                where = f"{layout} / {box_id}"
                if kind not in SEGMENTS:
                    errors.append(f"{where}: segment type {kind!r} is not one of {SEGMENTS}")
                    continue
                if kind in ("figure", "fact", "memo_data") and not CONCEPT.match(str(seg.get("concept", ""))):
                    errors.append(f"{where}: concept {seg.get('concept')!r} must be snake_case")
                if kind == "conditional" and not seg.get("when"):
                    errors.append(f"{where}: conditional needs 'when'")
                for memo, example in examples_of(seg).items():
                    if stem(memo) in memos and not find(layout, box_id, memo, example):
                        errors.append(f"{where}: example {str(example)[:50]!r} not found in {stem(memo)}")
                if kind == "table":
                    for row in seg.get("rows") or []:
                        for memo, example in (row.get("example") or {}).items():
                            if stem(memo) in memos and not find(layout, box_id, memo, example):
                                errors.append(f"{where}: table example {str(example)[:50]!r} (row {row.get('label')!r}) not found in {stem(memo)}")
        errors += new_checks(answer, context)
        errors += guide_quote_errors(answer, context["guide"])
        return errors

    return validate


PERIOD = re.compile(r"^(FY|CY|H[12]|Q[1-4])?\s?'?\d{2,4}\s?[AFEBP]?$|^(FY|CY)", re.I)


def defined_concepts(answer: dict) -> set[str]:
    out = {c.get("concept") for c in answer.get("extra_concepts") or []}
    for r in answer.get("recipes") or []:
        for seg in r.get("pattern") or []:
            if seg.get("type") in ("figure", "fact", "memo_data"):
                out.add(seg.get("concept"))
            out |= {row.get("concept") for row in seg.get("rows") or [] if isinstance(row, dict) and row.get("concept")}
            out |= {x.get("concept") for x in seg.get("series") or [] if isinstance(x, dict) and x.get("concept")}
    return out


def new_checks(answer: dict, context: dict) -> list[str]:
    """G1 paragraphs, G5 concrete tables, G6 charts and a complete catalogue. G7 is enforced by the master's
    boxes being required like any layout's."""
    errors = []
    defined = defined_concepts(answer)
    guide = norm(context["guide"] or "").lower()
    for r in answer.get("recipes") or []:
        layout, box_id = r.get("layout"), r.get("box_id")
        where = f"{layout} / {box_id}"
        pattern = r.get("pattern") or []
        memo_texts = context["box_texts"].get((layout, box_id), {})
        for seg in pattern:
            kind = seg.get("type")
            if "paragraph" in seg and not (isinstance(seg["paragraph"], int) and seg["paragraph"] >= 0):
                errors.append(f"{where}: paragraph must be 0, 1, 2...")
            if kind == "fixed":
                text = str(seg.get("text", ""))
                if PARA in text:
                    errors.append(f"{where}: fixed text contains the paragraph mark; split it into paragraphs")
                elif " / " in text and memo_texts and all(" / " not in t for t in memo_texts.values()):
                    errors.append(f"{where}: fixed text {text[:40]!r} has ' / ', which the memos write as a line break")
            for concept in seg.get("must_include") or []:
                if concept not in defined:
                    errors.append(f"{where}: must_include concept {concept!r} is not defined anywhere; "
                                  f"define it or add it to extra_concepts")
            if kind == "table":
                known = norm(" ".join([context["template_tables"].get((layout, box_id), "")]
                                      + list(memo_texts.values()))).lower()
                for row in seg.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    label = norm(row.get("label") or "").lower()
                    if row.get("label_type", "fixed") == "fixed" and label and label not in known and label not in guide:
                        errors.append(f"{where}: fixed row label {row.get('label')!r} is not written in the template, "
                                      f"the memos or the guide; use a real label or label_type model_writes")
                fixed_labels = {norm(row.get("label") or "").lower() for row in seg.get("rows") or []
                                if isinstance(row, dict) and row.get("label_type", "fixed") == "fixed"}
                memo_all = norm(" ".join(memo_texts.values())).lower()
                for label in context["template_labels"].get((layout, box_id), []):
                    n = norm(label).lower()
                    # a template row label the past memos also write is a fixed row of the firm's table
                    if len(n) > 2 and memo_all and re.search(rf"(^|[|\s]){re.escape(n)}([|\s]|$)", memo_all) and n not in fixed_labels:
                        errors.append(f"{where}: the template's row {label!r} is also in the past memos, so it is a fixed "
                                      f"row of this table: list it with label_type fixed")
                splits = context["period_split"].get((layout, box_id), {})
                if len(set(splits.values())) > 1:
                    asked = " ".join(str(q) for q in answer.get("questions") or []).lower()
                    if str(layout).lower() not in asked:
                        shown = ", ".join(f"{stem(src)}: {a} actual + {f} forecast" for src, (a, f) in splits.items())
                        errors.append(f"{where}: the period columns differ between materials ({shown}); add a question "
                                      f"for the person that names the layout {layout!r} and these options")
                if seg.get("header_type") in ("periods", "mixed"):
                    rule = seg.get("column_rule")
                    if not (isinstance(rule, dict) and all(isinstance(rule.get(k), int)
                                                           for k in ("actual_periods", "forecast_periods"))):
                        errors.append(f"{where}: a header with periods needs column_rule with integer "
                                      f"actual_periods and forecast_periods")
                    else:
                        periods = [h for h in seg.get("header") or [] if PERIOD.match(str(h).strip())]
                        if periods and len(periods) != rule["actual_periods"] + rule["forecast_periods"]:
                            errors.append(f"{where}: column_rule gives {rule['actual_periods']}+{rule['forecast_periods']} "
                                          f"periods but the header lists {len(periods)}")
            if kind == "chart":
                if seg.get("chart_type") not in CHART_TYPES:
                    errors.append(f"{where}: chart_type must be one of {CHART_TYPES}")
                if seg.get("categories") not in ("periods", "items"):
                    errors.append(f"{where}: chart categories must be periods or items")
                series = seg.get("series") or []
                if not series or any(not isinstance(x, dict) or not CONCEPT.match(str(x.get("concept", ""))) for x in series):
                    errors.append(f"{where}: chart needs series, each with a snake_case concept")
                # Only series every memo with a chart here names the same way are the firm's; series chosen per deal,
                # or named by a year or period ("2024", "2021 cohort"), are not required.
                per_memo = list(context["chart_series"].get((layout, box_id), {}).values())
                real = set.intersection(*per_memo) if per_memo else set()
                real = {r for r in real if not PERIOD.match(r) and not re.search(r"\b(19|20)\d{2}\b", r)}
                names = [norm(x.get("name") or "").lower() for x in series if isinstance(x, dict)]
                for r in sorted(real):
                    if not any(r in n or n in r for n in names if n):
                        errors.append(f"{where}: every past memo's chart here has a series {r!r}; include it, named as the "
                                      f"memos name it, with the concept of that data")
                seen = context["chart_types"].get((layout, box_id))
                if seen and seg.get("chart_type") not in seen:
                    errors.append(f"{where}: chart_type {seg.get('chart_type')!r} but the past memos use {sorted(seen)}")
        if context["box_types"].get((layout, box_id)) == "chart" and sum(1 for x in pattern if x.get("type") == "chart") != 1:
            errors.append(f"{where}: a chart box needs exactly one chart segment")
    for c in answer.get("extra_concepts") or []:
        if not CONCEPT.match(str(c.get("concept", ""))) or c.get("type") not in ("figure", "fact", "memo_data"):
            errors.append(f"extra concept {c!r} needs a snake_case concept and type figure, fact or memo_data")
    return errors


def recount_seen_in(answer: dict, context: dict) -> None:
    """seen_in is a fact code can count: memos whose box holds the fixed text."""
    for r in answer.get("recipes") or []:
        texts = context["box_texts"].get((r.get("layout"), r.get("box_id")), {})
        for seg in r.get("pattern") or []:
            if seg.get("type") == "fixed" and texts:
                seg["seen_in_counted"] = sum(1 for t in texts.values() if norm(seg.get("text", "")) in norm(t))
                seg["memos_with_box"] = len(texts)


def concept_catalogue(answer: dict) -> dict[str, dict]:
    catalogue: dict[str, dict] = {}
    for r in answer.get("recipes") or []:
        where = f"{r.get('layout')} / {r.get('box_id')}"
        for seg in r.get("pattern") or []:
            items = []
            if seg.get("type") in ("figure", "fact", "memo_data"):
                items.append((seg.get("concept"), seg.get("type"), seg.get("kind"), seg.get("examples") or {}, False))
            elif seg.get("type") == "chart":
                items += [(x.get("concept"), "figure", None, {}, True) for x in seg.get("series") or []
                          if isinstance(x, dict) and x.get("concept")]
            elif seg.get("type") == "table":
                items += [(row.get("concept"), "figure" if row.get("cells") == "figure" else row.get("cells"),
                           row.get("kind"), row.get("example") or {}, True) for row in seg.get("rows") or [] if row.get("concept")]
            for concept, kind, value_kind, examples, in_table in items:
                entry = catalogue.setdefault(concept, {"type": kind, "kind": value_kind, "used_in": [], "examples": defaultdict(set),
                                                       "single_values": defaultdict(set)})
                if where not in entry["used_in"]:
                    entry["used_in"].append(where)
                for memo, example in examples.items():
                    entry["examples"][stem(memo)].add(norm(example))
                    if not in_table:
                        entry["single_values"][stem(memo)].add(norm(example))
    for c in answer.get("extra_concepts") or []:
        if c.get("concept") and c["concept"] not in catalogue:
            catalogue[c["concept"]] = {"type": c.get("type"), "kind": c.get("kind"), "used_in": ["must_include"],
                                       "examples": defaultdict(set), "single_values": defaultdict(set)}
    return catalogue


def value_conflicts(catalogue: dict) -> list[str]:
    out = []
    for concept, entry in catalogue.items():
        if entry["type"] not in ("figure", "fact"):
            continue
        # Table cells differ by row and column by design; only figures written on their own are compared.
        for memo, values in entry["single_values"].items():
            if len({v.lower() for v in values}) > 1:
                out.append(f"{memo}: concept {concept!r} has different values {sorted(values)}")
    return out


# ------------------------------------------------------------------ review

def render(pattern: list[dict]) -> str:
    parts = []
    for seg in pattern:
        t = seg.get("type")
        if seg.get("paragraph"):
            parts.append(f"{PARA}{seg['paragraph']}")
        if t == "fixed":
            counted = f" ⟨{seg.get('seen_in_counted')}/{seg.get('memos_with_box')}⟩" if seg.get("memos_with_box") else ""
            parts.append(f"\"{seg.get('text')}\"{counted}")
        elif t in ("figure", "fact", "memo_data"):
            parts.append(f"[{seg.get('concept')}]")
        elif t == "conditional":
            parts.append(f"{{if {seg.get('when')}: \"{seg.get('text')}\"}}")
        elif t == "model_writes":
            inc = f" incl. {', '.join(seg['must_include'])}" if seg.get("must_include") else ""
            ex = f" · {len(seg['style_examples'])} style examples" if seg.get("style_examples") else ""
            parts.append(f"<model: {seg.get('what')}{inc}{ex}>")
        elif t == "person_writes":
            parts.append(f"<person: {seg.get('what')}>")
        elif t == "chart":
            series = ", ".join(str(x.get("concept", "?")) for x in seg.get("series") or [] if isinstance(x, dict))
            parts.append(f"CHART {seg.get('chart_type')} by {seg.get('categories')} ({seg.get('category_count')}) "
                         f"rule {seg.get('column_rule')} series [{series}]")
        elif t == "table":
            rows = "; ".join(f"{r.get('label')}=[{r.get('concept') or r.get('cells')}]" for r in seg.get("rows") or [])
            parts.append(f"TABLE {seg.get('header')} columns: {seg.get('columns')} rule {seg.get('column_rule')}; rows: {rows}")
    return " ".join(parts)


def review(firm: str, result: dict) -> str:
    a = result["answer"]
    layouts = load_step(firm, 3)["layouts"]
    master = load_step(firm, 3).get("master") or []
    roles = {(l["name"], b["id"]): b.get("role") for l in layouts + [{"name": MASTER, "boxes": master}]
             for b in l["boxes"]}
    out = [f"STEP 4 · Recipe for {firm}", "", "⟨n/m⟩ after fixed text = found in n of the m memos that have this box", ""]
    by_layout = defaultdict(list)
    for r in a.get("recipes") or []:
        by_layout[r.get("layout")].append(r)
    for layout, recipes in by_layout.items():
        out.append(f"LAYOUT {layout}")
        for r in recipes:
            out.append(f"  {r.get('box_id'):<7} {str(roles.get((layout, r.get('box_id')))):<26} {render(r.get('pattern') or [])[:400]}")
    cat = result["catalogue"]
    out += ["", f"CONCEPTS ({len(cat)}) — what the binding will connect to a deal record"]
    for concept, e in sorted(cat.items()):
        out.append(f"  {concept:<34} {e['type']:<10} {str(e.get('kind') or ''):<9} used in {len(e['used_in'])} boxes")
    out += ["", f"SAME CONCEPT, DIFFERENT VALUES IN ONE MEMO ({len(result['value_conflicts'])}, possible accidents)"]
    out += [f"  ? {c}" for c in result["value_conflicts"]]
    out += ["", f"UNMATCHED CONTENT ({len(a.get('unmatched') or [])})"]
    out += [f"  {u.get('memo')} slide {u.get('slide')} {u.get('item')!r}: {u.get('decision')} — {u.get('why')}" for u in a.get("unmatched") or []]
    out += ["", f"ACCIDENTS IN THE PAST MEMOS ({len(a.get('accidents') or [])})"] + [f"  - {x}" for x in a.get("accidents") or []]
    out += ["", f"QUESTIONS FOR THE PERSON ({len(a.get('questions') or [])})"] + [f"  ? {x}" for x in a.get("questions") or []]
    if result.get("unresolved"):
        out += ["", "CHECKS STILL FAILING (for the person)"] + [f"  ! {e}" for e in result["unresolved"]]
    att = result.get("attempts") or []
    out += ["", f"Model calls: {len(att)}, seconds {sum(x['seconds'] for x in att):.0f}, "
                f"tokens in {sum(x['prompt_tokens'] or 0 for x in att)}, out {sum(x['completion_tokens'] or 0 for x in att)}"]
    for i, x in enumerate(att, 1):
        out.append(f"  call {i}: {len(x['errors'])} failed checks" + (f" ({x['errors'][0][:110]}…)" if x["errors"] else ""))
    return "\n".join(out)


def run(firm: str, review_only: bool = False) -> None:
    text, images, context = build_package(firm)
    if review_only:
        result = load_step(firm, 4)
    else:
        result = call_with_checks(INSTRUCTIONS, text, images, make_validate(context))
        result["input_tokens_estimate"] = len(text) // 4
        save_result(firm, 4, result)
    recount_seen_in(result["answer"], context)
    catalogue = concept_catalogue(result["answer"])
    result["value_conflicts"] = value_conflicts(catalogue)
    result["catalogue"] = {k: {**{kk: vv for kk, vv in v.items() if kk != "single_values"},
                               "examples": {m: sorted(x) for m, x in v["examples"].items()}} for k, v in catalogue.items()}
    path = save_step(firm, 4, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    parser.add_argument("--review-only", action="store_true")
    args = parser.parse_args()
    run(args.firm, args.review_only)


if __name__ == "__main__":
    main()
