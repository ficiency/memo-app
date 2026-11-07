"""Step 5: rules. What the materials say that is logic, not looks: things the platform must compute or
watch in every memo. Each rule is an instance of a type from a fixed library, with parameters; what no
type can express goes to "unmapped", with its quote, as a candidate extension of the library.

Input package:
    guide                 full text + page images
    design file notes     text left outside the page (the template's own instructions)
    approved so far       sections (step 2), box roles (step 3), concepts (step 4), palette and text roles (step 1)
Code checks every rule against the library and against what earlier steps approved.

Usage (from backend/):
    python -m app.services.onboarding.step5_rules <firm> [--review-only]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from app.services.onboarding import slices
from app.services.onboarding.checks import guide_quote_errors
from app.services.onboarding.common import load_step, save_result, save_step
from app.services.onboarding.llm import call_with_checks

# type -> (required parameters, what it controls)
LIBRARY = {
    "linked_values": (("concepts", "sections"), "these values must be identical wherever they appear in these sections"),
    "derived_value": (("concept", "formula"), "a value computed from other concepts; formula may branch by case"),
    "conditional_statement": (("when", "section", "requirement"), "when a fact about the deal holds, a section must state something"),
    "cardinality": (("section", "what", "min", "max"), "how many items a section or box holds (min = max for exactly); "
                    "optional when_exceeded: where extra items go (a section) or 'split'"),
    "required_content": (("section", "must_state"), "a section must state these things (concepts or plain descriptions)"),
    "prohibited_content": (("scope", "must_not"), "something must not appear (scope: a section or 'all')"),
    "ordered_items": (("section", "what", "order"), "items of a list appear in this fixed order"),
    "location_restriction": (("what", "only_in"), "a kind of value may appear only in these sections (or section kinds), nowhere else"),
    "min_size_overflow": (("applies_to", "min_size_pt", "when_too_long"), "smallest allowed size and what to do when content does not fit"),
    "colour_usage": (("colour", "only_for"), "what a colour may be used for, and limits on it"),
    "native_content": (("content", "pictures_allowed_in"), "charts/tables must be native; where pictures are allowed"),
    "naming": (("entity", "variants"), "which form of a name is used in which sections"),
    "numbering": (("section", "scheme", "renumber_on_reorder"), "how instances or references are numbered, and what updates when order changes"),
    "freshness": (("what", "max_age"), "how recent a datum must be"),
}

INSTRUCTIONS = """You turn the rules in a firm's materials into checkable rules for its investment memos. Use only the
rule types of the LIBRARY below, each with its parameters. Refer to sections, concepts, colours and text roles
by the exact names in the APPROVED lists. A rule that no library type can express goes to "unmapped" with its
exact quote; do not stretch a type to fit it.

Only include rules the materials state (a quote is required).

Earlier steps own APPEARANCE: colours and their names, fonts, text sizes and minimum sizes by role, how
tables and charts look, number formats, citations, footer, section order and stages. Do not restate those.
This step owns LOGIC, and nothing earlier covers it:
- restrictions on where or for what a colour, a size or a kind of content may be used ("coral only for
  breaches", "pictures only in the appendix") — even when the palette describes the colour's use;
- what to do when content does not fit (split it, move it to another section; every option the materials
  give);
- counts, values that must match across sections, required or prohibited statements, fixed orders,
  conditions, numbering, freshness.
What a section must contain is only prose so far: turn every checkable part of it into rules. "unmapped" is
for logic the materials state that no library type can express.

Return JSON only:
{"rules": [{"type": "<library type>", "params": {<the type's parameters>}, "says": "<the rule in one plain line>",
            "evidence": {"source": "guide|template", "quote": "<exact words>", "ref": "<where, when template>"}}],
 "unmapped": [{"says": "<the rule in one plain line>", "evidence": {"source": "guide|template", "quote": "<exact words>"},
               "why_no_type": "<what the library lacks>"}]}
"""


def library_text() -> str:
    return "\n".join(f"  {name}: params {list(params)} — {what}" for name, (params, what) in LIBRARY.items())


def approved_lists(firm: str) -> dict:
    style, structure = load_step(firm, 1)["answer"], load_step(firm, 2)["answer"]
    layouts, recipe = load_step(firm, 3)["layouts"], load_step(firm, 4)
    return {
        "sections": [s["name"] for s in structure.get("sections") or []],
        "concepts": sorted(recipe.get("catalogue") or {}),
        "colours": [p.get("name") for p in style.get("palette") or []],
        "text_roles": [r.get("role") for r in style.get("text_roles") or []],
        "box_roles": sorted({b.get("role") for l in layouts for b in l["boxes"] if b.get("role")}),
    }


def build_package(firm: str):
    lists = approved_lists(firm)
    parts = [f"LIBRARY\n{library_text()}",
             "APPROVED\n" + "\n".join(f"  {k}: {', '.join(map(str, v))}" for k, v in lists.items())]
    guide = slices.guide_text(firm)
    if guide:
        parts.append(f"=== GUIDE (text; page images follow)\n{guide}")
    _, design = slices.design_source(firm)
    if design:
        notes = slices.off_page_notes(design)
        if notes:
            parts.append(f"=== NOTES OUTSIDE THE PAGE in the design file\n{notes}")
    return "\n\n".join(parts), slices.guide_images(firm), {"lists": lists, "guide": guide}


def make_validate(context: dict):
    lists = context["lists"]
    sections, concepts = set(lists["sections"]), set(lists["concepts"])
    colours = {c.lower() for c in lists["colours"]}
    roles = set(lists["text_roles"]) | set(lists["box_roles"])

    def as_list(value):
        return value if isinstance(value, list) else [value]

    def validate(answer: dict) -> list[str]:
        errors = []
        for i, rule in enumerate(answer.get("rules") or []):
            kind, params = rule.get("type"), rule.get("params") or {}
            where = f"rule {i} ({kind}: {str(rule.get('says'))[:50]!r})"
            if kind not in LIBRARY:
                errors.append(f"{where}: type is not in the library")
                continue
            for key in LIBRARY[kind][0]:
                if key not in params:
                    errors.append(f"{where}: missing parameter {key!r}")
            for key in ("section", "sections"):
                for name in as_list(params.get(key) or []):
                    if name and name not in sections and name != "all":
                        errors.append(f"{where}: section {name!r} does not exist")
            for key in ("concept", "concepts"):
                for name in as_list(params.get(key) or []):
                    if name and name not in concepts:
                        errors.append(f"{where}: concept {name!r} does not exist (add it in the recipe or use an existing one)")
            if kind == "colour_usage" and str(params.get("colour", "")).lower() not in colours:
                errors.append(f"{where}: colour {params.get('colour')!r} is not in the palette")
            if kind == "min_size_overflow":
                for name in as_list(params.get("applies_to") or []):
                    if name not in roles and name != "all":
                        errors.append(f"{where}: applies_to {name!r} is not a text or box role")
            if kind == "cardinality":
                for key in ("min", "max"):
                    if params.get(key) is not None and not isinstance(params.get(key), int):
                        errors.append(f"{where}: {key} must be a whole number or null")
            if not (rule.get("evidence") or {}).get("quote"):
                errors.append(f"{where}: needs a quote")
        for i, item in enumerate(answer.get("unmapped") or []):
            if not (item.get("evidence") or {}).get("quote"):
                errors.append(f"unmapped {i}: needs a quote")
        errors += guide_quote_errors(answer, context["guide"])
        return errors

    return validate


def review(firm: str, result: dict) -> str:
    a = result["answer"]
    out = [f"STEP 5 · Rules for {firm}", ""]
    by_type = defaultdict(list)
    for rule in a.get("rules") or []:
        by_type[rule.get("type")].append(rule)
    for kind in LIBRARY:
        for rule in by_type.get(kind, []):
            params = "; ".join(f"{k}={v}" for k, v in (rule.get("params") or {}).items())
            out.append(f"  {kind:<22} {rule.get('says')}")
            out.append(f"  {'':<22} {params[:240]}")
    out += ["", f"UNMAPPED ({len(a.get('unmapped') or [])}) — rules the library cannot express yet (candidate extensions)"]
    for item in a.get("unmapped") or []:
        out.append(f"  - {item.get('says')}  [needs: {item.get('why_no_type')}]")
    counts = {k: len(v) for k, v in by_type.items()}
    out += ["", f"Rules by type: {counts}"]
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
        result = load_step(firm, 5)
    else:
        result = call_with_checks(INSTRUCTIONS, text, images, make_validate(context))
        result["input_tokens_estimate"] = len(text) // 4
        save_result(firm, 5, result)
    path = save_step(firm, 5, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    parser.add_argument("--review-only", action="store_true")
    args = parser.parse_args()
    run(args.firm, args.review_only)


if __name__ == "__main__":
    main()
