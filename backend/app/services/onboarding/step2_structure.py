"""Step 2: structure. The sections of the memo, their status, layouts, how many times each appears,
what each must contain, the order rules, and which sections each stage requires.

Input package (only what exists):
    guide       full text + page images (images first, text to quote)
    design file every layout with its boxes; notes left outside the page (often the template's own instructions)
    memos       each memo's slides in order: number, layout, title
    step 1      memo name and stages (approved)
Code then checks the answer; failures go back to the model. Finally every memo is checked against
the proposed order rules: a memo that breaks them is reported as a possible accident in that memo.

Usage (from backend/):
    python -m app.services.onboarding.step2_structure <firm>
"""
from __future__ import annotations

import argparse
import re
import sys

from app.services.onboarding import slices
from app.services.onboarding.checks import guide_quote_errors
from app.services.onboarding.common import load_step, save_result, save_step
from app.services.onboarding.llm import call_with_checks

STATUSES = ("locked", "mandatory", "optional", "free")
CONSTRAINT = re.compile(r"^(first|last|position:\d+|after_all:(locked|mandatory|optional|free)|"
                        r"(after|before|immediately_after|immediately_before):.+)$")
TO_GENERATE = "to_generate"

INSTRUCTIONS = f"""You define the structure of a firm's investment memo from the materials it sent: which
sections it has, in what order, which are fixed and which are free, and what each stage requires.
The materials are listed at the top; some may be missing. Read the guide from its page images first
and use its text to quote.

Return JSON only, with this shape (angle brackets describe what goes there):
{{
 "sections": [{{"name": "<section name as the firm calls it>",
               "status": "<one of {', '.join(STATUSES)}>",
               "layouts": ["<exact layout name from the design file; when no design file has it, '{TO_GENERATE}:<the name the materials give it, or a short name>'>"],
               "count": "<'one', 'any', or a number>",
               "constraints": ["<order rules from this list: first | last | position:<n> | after:<section> |
                               before:<section> | immediately_after:<section> | immediately_before:<section> |
                               after_all:<status>>"],
               "must_contain": "<what the section must contain, as the materials say>",
               "numbering": "<how instances are numbered or tagged, or null>",
               "evidence": <E>}}],
 "stages": [{{"name": "<a stage name from step 1>", "required": ["<section names>"], "optional": ["<section names>"],
             "notes": "<other stage rules, or null>", "evidence": <E>}}],
 "conflicts": [{{"topic": "<what differs>", "options": [{{"source": "<guide|template|memo>", "value": "<what it says>",
                "evidence": <E>}}], "recommendation": "<which option and why in one line>"}}]
}}
<E> is {{"source": "<guide|template|memo|assumed>", "quote": "<exact words from the guide, when source is guide>",
        "ref": "<where in the PowerPoint, when source is template or memo>"}}.

Rules:
- Section names in constraints and stages must be names from your own sections list.
- Every layout the memos use must belong to some section (free sections such as an appendix may list
  several layouts).
- A layout the materials describe or name but no design file contains is '{TO_GENERATE}:<name>'. List
  every such layout the materials name, under the section that uses it.
- Past memos show what people did, mistakes included: when a memo differs from the guide or the
  template, report a conflict rather than copying the memo.
- Where nothing in the materials says it, use source "assumed".
"""


def build_package(firm: str) -> tuple[str, list, dict]:
    step0, step1 = load_step(firm, 0), load_step(firm, 1)["answer"]
    present = sorted({m["kind"] for m in step0["materials"]})
    ident = step1.get("identity") or {}
    stages = [s.get("name") for s in ident.get("stages") or []]
    parts = [f"MATERIALS SENT: {', '.join(present)}. MISSING: {', '.join(step0['missing']) or 'nothing'}.",
             f"APPROVED IN STEP 1: memo name {(ident.get('memo_name') or {}).get('value')!r}; stages {stages}"]
    guide = slices.guide_text(firm)
    if guide:
        parts.append(f"=== GUIDE (text; page images follow)\n{guide}")
    material, data = slices.design_source(firm)
    layouts: set[str] = set()
    if data:
        layouts = {l["name"] for l in data["layouts"]}
        parts.append(f"=== LAYOUTS in the design file ({material['kind']}: {material['file']})\n{slices.layout_index(data)}")
        notes = slices.off_page_notes(data)
        if notes:
            parts.append(f"=== NOTES OUTSIDE THE PAGE in the design file\n{notes}")
    memos = {}
    for memo in slices.materials(firm, "memo"):
        memo_data = slices.raw(firm, memo)
        memos[memo["file"]] = memo_data
        parts.append(f"=== MEMO OUTLINE {memo['file']}\n{slices.outline(memo_data)}")
    context = {"guide": guide, "layouts": layouts, "stages": set(stages), "memos": memos,
               "has_pptx": bool(data)}
    return "\n\n".join(parts), slices.guide_images(firm), context


def make_validate(context: dict):
    def validate(answer: dict) -> list[str]:
        errors = []
        sections = answer.get("sections") or []
        names = [s.get("name") for s in sections]
        if not sections:
            errors.append("sections is empty")
        if len(names) != len(set(names)):
            errors.append("section names must be unique")
        covered = set()
        for s in sections:
            where = f"section {s.get('name')!r}"
            if s.get("status") not in STATUSES:
                errors.append(f"{where}: status must be one of {STATUSES}")
            for layout in s.get("layouts") or []:
                if str(layout).startswith(TO_GENERATE):
                    continue
                if layout not in context["layouts"]:
                    errors.append(f"{where}: layout {layout!r} does not exist in the design file")
                covered.add(layout)
            if not s.get("layouts"):
                errors.append(f"{where}: needs at least one layout (or '{TO_GENERATE}')")
            count = str(s.get("count"))
            if count not in ("one", "any") and not count.isdigit():
                errors.append(f"{where}: count must be 'one', 'any' or a number")
            for c in s.get("constraints") or []:
                if not CONSTRAINT.match(str(c)):
                    errors.append(f"{where}: constraint {c!r} is not in the allowed form")
                elif ":" in c and not c.startswith(("position:", "after_all:")) and c.split(":", 1)[1] not in names:
                    errors.append(f"{where}: constraint {c!r} names a section that does not exist")
        for memo, data in context["memos"].items():
            for layout in sorted({slide["layout"] for slide in data["slides"]} - covered):
                errors.append(f"layout {layout!r} used in {memo} belongs to no section")
        for st in answer.get("stages") or []:
            if context["stages"] and st.get("name") not in context["stages"]:
                errors.append(f"stage {st.get('name')!r} is not one of the stages approved in step 1: {sorted(context['stages'])}")
            for n in (st.get("required") or []) + (st.get("optional") or []):
                if n not in names:
                    errors.append(f"stage {st.get('name')!r} lists section {n!r}, which does not exist")
        errors += guide_quote_errors(answer, context["guide"])
        return errors

    return validate


def memo_order_findings(answer: dict, memos: dict) -> list[str]:
    """Check each memo's slide order against the proposed order rules. A memo is mapped to sections
    through the layouts each section uses; findings are possible accidents in the memo."""
    sections = answer.get("sections") or []
    by_layout = {}
    for s in sections:
        for layout in s.get("layouts") or []:
            by_layout.setdefault(layout, s["name"])
    status = {s["name"]: s.get("status") for s in sections}
    findings = []
    for memo, data in memos.items():
        order = [by_layout.get(slide["layout"]) for slide in data["slides"]]
        positions: dict[str, list[int]] = {}
        for i, name in enumerate(order, 1):
            if name:
                positions.setdefault(name, []).append(i)
        n = len(order)
        for s in sections:
            name, mine = s["name"], positions.get(s["name"])
            if not mine:
                if s.get("status") in ("locked", "mandatory"):
                    findings.append(f"{memo}: {status[name]} section {name!r} is missing")
                continue
            if str(s.get("count")) == "one" and len(mine) > 1:
                findings.append(f"{memo}: section {name!r} should appear once, appears on slides {mine}")
            for c in s.get("constraints") or []:
                kind, _, arg = str(c).partition(":")
                other = positions.get(arg) or []
                ok = True
                if kind == "first":
                    ok = mine[0] == 1
                elif kind == "last":
                    ok = mine[-1] == n
                elif kind == "position":
                    ok = mine[0] == int(arg)
                elif kind == "after" and other:
                    ok = mine[0] > max(other)
                elif kind == "before" and other:
                    ok = mine[-1] < min(other)
                elif kind == "immediately_after" and other:
                    ok = mine[0] == max(other) + 1
                elif kind == "immediately_before" and other:
                    ok = mine[-1] == min(other) - 1
                elif kind == "after_all":
                    group = [p for nm, ps in positions.items() if status.get(nm) == arg and nm != name for p in ps]
                    ok = not group or mine[0] > max(group)
                if not ok:
                    findings.append(f"{memo}: section {name!r} (slides {mine}) breaks {c!r}")
    return findings


def review(firm: str, result: dict) -> str:
    a = result["answer"]
    out = [f"STEP 2 · Structure for {firm}", "", "SECTIONS"]
    for s in a.get("sections") or []:
        layouts = ", ".join(s.get("layouts") or [])
        rules = ", ".join(s.get("constraints") or []) or "-"
        out.append(f"  {s.get('status', ''):<9} x{str(s.get('count')):<4} {s.get('name')!r:<38} layouts: {layouts}")
        out.append(f"  {'':<15}order: {rules}" + (f"; numbering: {s['numbering']}" if s.get("numbering") else ""))
        out.append(f"  {'':<15}must contain: {s.get('must_contain')}  [{(s.get('evidence') or {}).get('source')}]")
    out += ["", "STAGES"]
    for st in a.get("stages") or []:
        out.append(f"  {st.get('name')}: required {st.get('required')}")
        if st.get("optional"):
            out.append(f"      optional {st.get('optional')}")
        if st.get("notes"):
            out.append(f"      notes: {st['notes']}")
    out += ["", f"CONFLICTS FOR THE PERSON ({len(a.get('conflicts') or [])})"]
    for conflict in a.get("conflicts") or []:
        out.append(f"  ? {conflict.get('topic')}")
        for o in conflict.get("options") or []:
            out.append(f"      {o.get('source')}: {o.get('value')}")
        out.append(f"      recommended: {conflict.get('recommendation')}")
    out += ["", f"PAST MEMOS AGAINST THIS STRUCTURE ({len(result['memo_findings'])} findings)"]
    out += [f"  - {f}" for f in result["memo_findings"]] or ["  (no memos, or no findings)"]
    if result["unresolved"]:
        out += ["", "CHECKS STILL FAILING (for the person)"] + [f"  ! {e}" for e in result["unresolved"]]
    att = result["attempts"]
    out += ["", f"Model calls: {len(att)}, seconds {sum(x['seconds'] for x in att):.0f}, "
                f"tokens in {sum(x['prompt_tokens'] or 0 for x in att)}, out {sum(x['completion_tokens'] or 0 for x in att)}"]
    for i, x in enumerate(att, 1):
        out.append(f"  call {i}: {len(x['errors'])} failed checks" + (f" ({x['errors'][0][:100]}…)" if x["errors"] else ""))
    return "\n".join(out)


def run(firm: str) -> None:
    text, images, context = build_package(firm)
    result = call_with_checks(INSTRUCTIONS, text, images, make_validate(context))
    result["memo_findings"] = memo_order_findings(result["answer"], context["memos"])
    result["input_tokens_estimate"] = len(text) // 4
    path = save_step(firm, 2, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    run(parser.parse_args().firm)


if __name__ == "__main__":
    main()
