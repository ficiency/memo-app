"""Pilot: ask a model what a firm writes in each box of its memos, then check the answer with code.

Input: the compact views (extractors.pptx_view) of one firm's past memos, plus its template view if
there is one. The model returns, per section and box, a pattern of segments:

    fixed          words the firm writes in every memo
    figure         a number that must come from the deal record (with its concept)
    model_writes   deal-specific prose a model would draft
    person_writes  something only a person can write (names, dates, judgements)

Code then checks the answer:
    - every figure the model quotes exists, letter for letter, in that memo's slide
    - within a memo, every figure with the same concept has the same value
    - every slide of every memo was covered by some section

Usage (from docs/pilots):
    python memo_patterns.py run <out-name> <memo.view.txt>... [--template <template.view.txt>]
    python memo_patterns.py check <out-name>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"

INSTRUCTIONS = """You read the past investment memos of one firm (and its template, if given) and work out
what the firm writes in each box, so that a new memo can be generated for a new deal.

Each memo is given as a compact view grouped by layout: every box of every layout, with what each
slide put in it, and boxes people added by hand. Layout names are hints only: slides may be in a
different order in each memo, and people make mistakes.

Work section by section. A section is one kind of slide that the memos share (for example the
recommendation slide). Match the slides of each memo that belong to the same section, even when
their order or layout differ. For each box of the section, give its pattern as a list of segments:

- {"type": "fixed", "text": "...", "seen_in": <number of memos with this exact text>,
   "confidence": "high|low"}: words the firm writes the same way in every memo.
   Before calling text fixed, ask: "if the next deal had different numbers and a different outcome,
   would this text still be true?" If not, it is not fixed: it is a conclusion about these deals
   (model_writes) or a conditional phrase. Use the guide and the template's instructions: a box the
   firm describes as a conclusion or a statement about the deal is written per deal, even when the
   memos happen to agree. Use "low" when you are unsure.
- {"type": "conditional", "text": "...", "when": "..."}: words the firm adds only when something about
   the deal is true (for example a phrase that appears only when a threshold is missed).
- {"type": "figure", "concept": "...", "kind": "money|percent|multiple|count|date|period|ratio|other",
   "examples": {"<memo file>": "<the figure exactly as written>"}}: a number about the deal that must
   come from the deal record. "concept" is a short snake_case name for what the number means
   (cheque_size, pre_money_valuation, moic_base). Use the same concept name for the same meaning everywhere.
- {"type": "fact", "concept": "...", "examples": {"<memo file>": "<exactly as written>"}}: a non-numeric
   fact about the deal that the deal record holds (company name, round, instrument, sector, adviser).
- {"type": "model_writes", "what": "..."}: deal-specific prose a model would draft (say what it is).
- {"type": "person_writes", "what": "..."}: something only a person can supply (team names, a vote, a judgement).

- {"type": "table", "header": [<header cells as written>], "header_type": "fixed|periods|mixed",
   "columns": "<what the columns are, e.g. three actual and three forecast fiscal years>",
   "rows": [{"label": "<row label as written>", "label_type": "fixed|fact|model_writes|person_writes",
             "cells": "figure|fact|model_writes|person_writes|mixed", "concept": "<snake_case, for figure cells>",
             "kind": "<as for figure>", "example": {"<memo file>": "<ONE cell of this row, exactly as written>"}}],
   "row_count": "fixed|varies", "notes": "..."}: a table box. Describe its structure; do not copy rows.
   Give one real cell per row as the example, never a row joined together.

Page furniture is not about the deal: page numbers, footer labels and slide counters are "fixed" or
left out, never "figure". Quote every figure, fact and table example exactly as it is written in that
memo, character for character.
Also list, per section, anything that looks like a one-off accident rather than the firm's practice.

Return JSON only:
{"sections": [{"section": "...", "slides": {"<memo file>": [slide numbers]},
               "boxes": [{"box": "<box name>", "pattern": [segments], "notes": "..."}],
               "accidents": ["..."]}],
 "slides_not_in_any_section": {"<memo file>": [slide numbers]}}
"""


def run(name: str, views: list[Path], template: Path | None, guide: Path | None) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    parts = []
    if guide:
        parts.append(f"=== GUIDE {guide.name}\n{guide.read_text(encoding='utf-8')}")
    if template:
        parts.append(f"=== TEMPLATE {template.name}\n{template.read_text(encoding='utf-8')}")
    for view in views:
        parts.append(f"=== MEMO {view.name}\n{view.read_text(encoding='utf-8')}")
    started = time.time()
    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": "\n\n".join(parts)}],
        response_format={"type": "json_object"},
    )
    seconds = round(time.time() - started, 1)
    answer = json.loads(response.choices[0].message.content)
    usage = response.usage.model_dump() if response.usage else {}
    record = {"model": model, "seconds": seconds, "usage": usage,
              "memos": [v.name for v in views], "template": template.name if template else None,
              "guide": guide.name if guide else None, "answer": answer}
    target = OUT / f"{name}.patterns.json"
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(f"{target}  model={model} seconds={seconds} usage={usage}\n")


def slide_texts(reader_json: Path) -> dict[int, str]:
    """All text on each slide (boxes, table cells, chart values), for literal checks."""
    data = json.loads(reader_json.read_text(encoding="utf-8"))

    def walk(element) -> list[str]:
        out = [element.get("text") or ""]
        for row in (element.get("table") or {}).get("rows", []):
            out += [cell.get("text", "") for cell in row["cells"]]
        for plot in (element.get("chart") or {}).get("plots", []):
            out += [str(v) for s in plot["series"] for v in s["values"]]
        for child in element.get("children", []):
            out += walk(child)
        return out

    return {s["number"]: " ".join(t for e in s["elements"] for t in walk(e)) for s in data["slides"]}


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def stem(name: str) -> str:
    """The model may name a memo by its view file or by the original .pptx; both share the stem."""
    return re.sub(r"(\.view\.txt|\.pptx|\.json)$", "", name)


def by_stem(mapping: dict) -> dict:
    return {stem(k): v for k, v in (mapping or {}).items()}


def check(name: str) -> None:
    record = json.loads((OUT / f"{name}.patterns.json").read_text(encoding="utf-8"))
    answer = record["answer"]
    memos = [stem(n) for n in record["memos"]]
    texts = {m: slide_texts(OUT / f"{m}.json") for m in memos}
    for section in answer.get("sections", []):
        section["slides"] = by_stem(section.get("slides"))

    figures, missing, concepts, low = 0, [], {}, []
    covered = {m: set() for m in memos}
    for section in answer.get("sections", []):
        for memo, numbers in section.get("slides", {}).items():
            if memo in covered:
                covered[memo].update(numbers)
        for box in section.get("boxes", []):
            # Table rows carry one example cell each; check them like figures.
            segments = []
            for seg in box.get("pattern", []):
                if seg.get("type") == "table":
                    segments += [{"type": "figure", "concept": row.get("concept") or row.get("label"),
                                  "examples": row.get("example") or {}} for row in seg.get("rows", [])]
                else:
                    segments.append(seg)
            for seg in segments:
                if seg.get("type") == "fixed" and seg.get("confidence") == "low":
                    low.append(f"{section['section']} / {box['box']}: {seg.get('text')!r}")
                if seg.get("type") not in ("figure", "fact"):
                    continue
                for memo, example in by_stem(seg.get("examples")).items():
                    if memo not in texts:
                        continue
                    figures += 1
                    slides = section.get("slides", {}).get(memo, [])
                    haystack = normalise(" ".join(texts[memo].get(n, "") for n in slides))
                    if normalise(str(example)) not in haystack:
                        missing.append(f"{section['section']} / {box['box']}: {memo} {example!r} not found on slides {slides}")
                    concepts.setdefault((memo, seg.get("concept")), set()).add(normalise(str(example)))

    conflicts = [f"{memo}: concept {concept!r} has {sorted(values)}" for (memo, concept), values in concepts.items() if len(values) > 1]
    uncovered = {m: sorted(set(texts[m]) - covered[m]) for m in memos}
    usage = record.get("usage", {})
    print(f"model={record['model']} seconds={record['seconds']} prompt_tokens={usage.get('prompt_tokens')} "
          f"completion_tokens={usage.get('completion_tokens')}")
    print(f"sections={len(answer.get('sections', []))} figure/fact examples={figures} not found={len(missing)} "
          f"concepts with different values={len(conflicts)} low-confidence fixed text={len(low)}")
    for line in low:
        print("  LOW CONFIDENCE FIXED  " + line)
    for line in missing:
        print("  NOT FOUND  " + line)
    for line in conflicts:
        print("  SAME CONCEPT, DIFFERENT VALUES  " + line)
    for memo, slides in uncovered.items():
        if slides:
            print(f"  SLIDES IN NO SECTION  {memo}: {slides}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("views", nargs="+", type=Path)
    r.add_argument("--template", type=Path)
    r.add_argument("--guide", type=Path)
    c = sub.add_parser("check")
    c.add_argument("name")
    args = parser.parse_args()
    if args.command == "run":
        run(args.name, args.views, args.template, args.guide)
    else:
        check(args.name)


if __name__ == "__main__":
    main()
