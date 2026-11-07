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

Usage (from docs/pilots):
    python -m onboarding_pilot.step4_recipe <firm> [--review-only]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from onboarding_pilot import slices
from onboarding_pilot.checks import guide_quote_errors
from onboarding_pilot.common import load_step, save_result, save_step
from onboarding_pilot.llm import call_with_checks

SEGMENTS = ("fixed", "figure", "fact", "memo_data", "conditional", "model_writes", "person_writes", "table")
CONCEPT = re.compile(r"^[a-z][a-z0-9_]*$")

INSTRUCTIONS = """You write the recipe of a firm's investment memo: for every content box of every layout,
what goes in it, so that a new memo can be generated for a new deal. Some materials may be missing; the
list is at the top.

For each layout you get its section, what the section must contain, and its boxes (id, role, type, the
template's prompt text). When there are past memos, you also get, box by box, what each memo wrote in that
box, plus anything people added on the slide outside the layout's boxes ("unmatched").

Give each box a pattern, a list of segments:
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
   "row_count": "<fixed number|varies>", "notes": "..."}
- Use the same concept name for the same meaning in every box and layout.
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
 "unmatched": [{"memo": "...", "slide": <n>, "item": "<name>", "decision": "belongs_to:<box id>|deal_specific|accident", "why": "..."}],
 "accidents": ["<anything in the past memos that looks like a one-off mistake, and why>"],
 "questions": ["<what the materials leave open for the person>"]}"""


# ------------------------------------------------------------------ package

def content_boxes(layout: dict) -> list[dict]:
    """Boxes that need a recipe: text, tables and charts that are not fixed words of the firm."""
    return [b for b in layout["boxes"] if b.get("type") in ("text", "table", "chart") and not b.get("text")]


def element_text(el: dict) -> str:
    if el.get("table"):
        return " || ".join(" | ".join(c.get("text", "").replace("\n", " / ") for c in row["cells"] if not c.get("spanned"))
                           for row in el["table"]["rows"])
    if el.get("chart"):
        out = [f"CHART {el['chart']['type']}"]
        for plot in el["chart"]["plots"]:
            out.append(f"categories {plot['categories']}")
            out += [f"series {s['name']!r} {s['values']}" for s in plot["series"]]
        return " ".join(out)
    return (el.get("text") or "").replace("\n", " / ")


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


def build_package(firm: str):
    step0 = load_step(firm, 0)
    structure, layouts = load_step(firm, 2)["answer"], load_step(firm, 3)["layouts"]
    sections = {s["name"]: s for s in structure.get("sections") or []}
    memos = memo_slides(firm)
    _, design = slices.design_source(firm)
    page = design["page"] if design else {"width_in": 1e9, "height_in": 1e9}
    present = sorted({m["kind"] for m in step0["materials"]})
    parts = [f"MATERIALS SENT: {', '.join(present)}. MISSING: {', '.join(step0['missing']) or 'nothing'}."]
    box_texts: dict[tuple, dict[str, str]] = defaultdict(dict)   # (layout, box id) -> memo -> text
    slide_texts: dict[tuple, dict[str, str]] = defaultdict(dict)  # (layout) -> memo -> all text on its slides
    required: dict[str, list[str]] = {}
    for layout in layouts:
        boxes = content_boxes(layout)
        required[layout["name"]] = [b["id"] for b in boxes]
        section = sections.get(layout.get("section"), {})
        lines = [f"=== LAYOUT {layout['name']!r} · section {layout.get('section')!r} ({section.get('status')}): "
                 f"must contain {section.get('must_contain')}"]
        for b in layout["boxes"]:
            if b.get("text"):
                lines.append(f"  fixed {b['type']} {b.get('role')}: {b['text'][:80]!r}")
        by_idx = {(b.get("placeholder") or {}).get("idx"): b for b in boxes if b.get("placeholder")}
        for b in boxes:
            what = b.get("prompt") or b.get("what") or ""
            lines.append(f"  BOX {b['id']} {b['type']} role={b.get('role')}" + (f" prompt={what[:120]!r}" if what else ""))
        for memo, slides_ in memos.items():
            for slide in slides_:
                if slide["layout"] != layout["name"]:
                    continue
                matched, unmatched = match_slide(slide, page)
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
    context = {"guide": guide, "required": required, "box_texts": box_texts, "slide_texts": slide_texts,
               "memos": list(memos)}
    return "\n\n".join(parts), slices.guide_images(firm), context


# ------------------------------------------------------------------ checks

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("’", "'").replace("−", "-")).strip()


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
        errors += guide_quote_errors(answer, context["guide"])
        return errors

    return validate


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
        elif t == "table":
            rows = "; ".join(f"{r.get('label')}=[{r.get('concept') or r.get('cells')}]" for r in seg.get("rows") or [])
            parts.append(f"TABLE {seg.get('header')} columns: {seg.get('columns')}; rows: {rows}")
    return " ".join(parts)


def review(firm: str, result: dict) -> str:
    a = result["answer"]
    layouts = load_step(firm, 3)["layouts"]
    roles = {(l["name"], b["id"]): b.get("role") for l in layouts for b in l["boxes"]}
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
