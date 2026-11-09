"""Measure 3: overflow on a long-content set we write, on every firm.

The brief (§05): "Report, for each firm: ... overflow on a long-content set you write", and (§02) "text measured
in the real fonts; long content continues or splits by the firm's rules, and where it cannot fit the system
says so".

Per firm, on a new memo made through the API: the boxes people and the model write (titles, commentary, labels,
table cells) get text of six lengths (one short sentence to eight), written over the memo's websocket as the canvas writes it. The set is
fixed (the same words for every firm), not made from the system's own measure. Then the memo is exported through
the API and PowerPoint itself is the judge: it opens the file and says, for each box, whether its text runs past
the box, at what size it is drawn, and whether all of it is there. Each written box is then:
  fits            PowerPoint lays it out inside the box at its own size
  shrunk to fit   drawn smaller (never below the firm's minimum for its role) and inside the box
  reported        it does not fit even at the smallest size: the memo has a "does not fit" pending for it and the
                  canvas marks it (and PowerPoint indeed runs past the box)
  cut silently    it runs past the box, or text is missing, and nothing says so: must be 0
  continued       carried on to another slide by the firm's rules: not built yet (counted, 0)
Every box of the memo (not only the written ones) is also compared: the system's measure against PowerPoint's
on fits / does not fit.

Needs PowerPoint (Windows) to judge. Usage (from the repo root):  backend/.venv/Scripts/python evals/overflow_suite.py [--firm f]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import REPO, Person, Server, latest_spec_and_record  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

PORT = 8013
WORK = REPO / "backend" / ".work" / "evals" / "overflow"
SENTENCE = ("The business keeps compounding recurring revenue while holding gross margin steady, and the committee "
            "should weigh the concentration in its largest customers against the depth of its product moat. ")
SET = {                                                    # the long-content set: the same words for every firm
    "short": "Growth stays ahead of plan.",
    "medium": SENTENCE.strip(),
    "two sentences": (SENTENCE * 2).strip(),         # around a box's room: where shrinking to the role's minimum decides
    "long": (SENTENCE * 3).strip(),
    "five sentences": (SENTENCE * 5).strip(),
    "very long": (SENTENCE * 8).strip(),
}
# boxes whose role may get smaller (its minimum below its own size) get a ladder of lengths around a box's room,
# where shrinking decides whether they fit: fixed word counts, not the system's measure
WORDS = (SENTENCE * 8).split()
LADDER = [45, 60, 75, 90, 105, 120, 140, 160, 180]
CELL_SET = {"short": "In line", "long": "Subject to diligence on the customer contracts, the change of control clauses and the data processing terms " * 2}


def stage_for(firm: str) -> str:
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc())).first()
        stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id)).all()
        return max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id,
                                                                                    S.StageSection.required)).all())).name


POWERPOINT = r"""
param($file)
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($file, $true, $false, $false)
$rows = @()
foreach ($slide in $pres.Slides) { foreach ($sh in $slide.Shapes) {
  if ($sh.Name -notlike 'memo:*') { continue }
  if ($sh.HasTable) {
    $min = 999; $text = ''
    for ($r = 1; $r -le $sh.Table.Rows.Count; $r++) { for ($c = 1; $c -le $sh.Table.Columns.Count; $c++) {
      $tr = $sh.Table.Cell($r, $c).Shape.TextFrame2.TextRange; $text += $tr.Text + '|'
      if ($tr.Text.Length -gt 0 -and $tr.Font.Size -lt $min) { $min = $tr.Font.Size } } }
    $rows += [pscustomobject]@{ name = $sh.Name; kind = 'table'; height = $sh.Height; bound = $sh.Height; top = 0; bottom = 0; size = $min; text = $text }
  } elseif ($sh.HasTextFrame -and $sh.TextFrame2.HasText) {
    $tf = $sh.TextFrame2; $tr = $tf.TextRange; $min = 999
    foreach ($run in $tr.Runs()) { if ($run.Font.Size -lt $min) { $min = $run.Font.Size } }
    $rows += [pscustomobject]@{ name = $sh.Name; kind = 'text'; height = $sh.Height; bound = $tr.BoundHeight; top = $tf.MarginTop; bottom = $tf.MarginBottom; size = $min; text = $tr.Text }
  } } }
$pres.Close(); $pp.Quit()
$rows | ConvertTo-Json -Compress -Depth 3
"""


def powerpoint_says(pptx: Path) -> dict:
    """PowerPoint's own layout of every memo box: id → {kind, overflow (in), size, text}."""
    script = WORK / "judge.ps1"
    script.write_text(POWERPOINT, encoding="utf-8")
    raw = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(pptx.resolve())],
                         capture_output=True, text=True, check=True, timeout=600).stdout
    rows = json.loads(raw, strict=False) if raw.strip() else []   # PowerPoint's line breaks are control characters
    rows = rows if isinstance(rows, list) else [rows]
    out = {}
    for r in rows:
        eid = r["name"].split(":")[1].split("@")[0]
        out[eid] = {"kind": r["kind"], "bound": r["bound"] / 72, "room": (r["height"] - r["top"] - r["bottom"]) / 72,
                    "size": r["size"], "text": r["text"]}
    return out


def text_of(e: dict) -> str:
    return "\n".join("".join(x["text"] for x in p["segments"]) for p in e["paragraphs"])


async def run_firm(server: Server, firm: str) -> dict:
    p = Person(server, f"alex@{firm}.demo")
    version, revision, _ = latest_spec_and_record(p)
    status, made = p.post("/memos", {"spec_version_id": version, "record_revision_id": revision, "stage": stage_for(firm),
                                     "memo_date": "2026-10-15", "deal_team": "Lead (lead); Associate"})
    assert status == 200, made
    memo_id = made["memo_id"]
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    await p.join(memo_id)
    written: dict = {}                                     # element id → (length, what, the text, its own size)
    try:
        return await judge_firm(p, firm, memo_id, folder, written)
    finally:
        await p.leave()
        from sqlalchemy import delete
        import uuid as _uuid
        from app.db.memo import Memo
        with SessionLocal() as s:
            s.execute(delete(Memo).where(Memo.id == _uuid.UUID(memo_id)))
            s.commit()


async def judge_firm(p: Person, firm: str, memo_id: str, folder: Path, written: dict) -> dict:
    """Write the long-content set into the memo, export it, and have PowerPoint judge every box."""
    view = (await p.aget(f"/memos/{memo_id}"))[1]
    # the boxes people and the model write: editable text with no figure in it, and table cells that take words
    targets, cells = [], []
    for s in view["slides"]:
        for e in s["elements"]:
            if e.get("master") or e["firm_lock"] == "fixed":
                continue
            segs = [x for p_ in e["paragraphs"] for x in p_["segments"]]
            if e["type"] == "text" and segs and any(x["kind"] in ("model_draft", "person_slot") for x in segs) and \
                    not any(x["kind"] == "chip" for x in segs):
                targets.append((s["position"] + 1, e))
            if e["type"] == "table" and e.get("table"):
                for c in e["table"]["cells"]:
                    cs = [x for p_ in c["paragraphs"] for x in p_["segments"]]
                    if c["kind"] != "fixed" and cs and all(x["kind"] in ("model_draft", "person_slot", "text") for x in cs) and \
                            any(x["kind"] in ("model_draft", "person_slot") for x in cs):
                        cells.append((s["position"] + 1, e, c))
    lengths, roles = list(SET), view["spec"]["roles"]
    shrinkable = []                                        # boxes whose role may get smaller: the ladder pass below
    for i, (slide, e) in enumerate(targets):
        role = roles.get(e.get("role") or "") or {}
        own = max([p_["style"].get("size_pt") or 0 for p_ in e["paragraphs"]] + [0]) or role.get("size_pt") or 12
        if role.get("min_size_pt") and role["min_size_pt"] < own:
            shrinkable.append((slide, e, own, role["min_size_pt"]))
        length = lengths[i % len(lengths)]
        r = await p.op({"type": "set_text", "target": e["id"], "base_revision": e["revision"], "payload": {"text": SET[length]}})
        if r["status"] == "applied":
            written[e["id"]] = {"length": length, "what": f"slide {slide} · {e.get('name') or e.get('role') or 'box'}", "text": SET[length]}
    seen_tables = set()
    for slide, e, c in cells:
        if e["id"] in seen_tables:                     # one cell per table, so each table is one case
            continue
        length = list(CELL_SET)[len(seen_tables) % len(CELL_SET)]
        current = next(x for s in (await p.aget(f"/memos/{memo_id}"))[1]["slides"] for x in s["elements"] if x["id"] == e["id"])
        r = await p.op({"type": "set_cell", "target": e["id"], "base_revision": current["revision"],
                        "payload": {"row": c["row"], "col": c["col"], "text": CELL_SET[length].strip()}})
        if r["status"] == "applied":
            seen_tables.add(e["id"])
            written[e["id"]] = {"length": f"cell {length}", "what": f"slide {slide} · {e.get('name') or 'table'} (a cell)",
                                "text": CELL_SET[length].strip()}
    view, pend, judged = await export_and_judge(p, memo_id, folder / "long_content.pptx")
    elements = {e["id"]: e for s in view["slides"] for e in s["elements"]}

    cases, agree, compared = [], 0, 0
    for eid, e in elements.items():
        pp, fit = judged.get(eid), e.get("fit")
        if pp is None or fit is None or pp["kind"] != ("table" if e["type"] == "table" else "text"):
            continue
        compared += 1
        agree += overflows(pp, e) == (not fit["fits"])
        if eid in written:
            cases.append({"what": written[eid]["what"], "length": written[eid]["length"],
                          **classify(e, pp, pend, written[eid]["text"])})

    # the ladder pass: the boxes that may get smaller, written longer and longer (fixed word counts), each step
    # exported and judged by PowerPoint: where they fit at their own size, where they fit only smaller, where they
    # are reported; never cut silently
    ladder = []
    for n in LADDER:
        current = {e["id"]: e for s in (await p.aget(f"/memos/{memo_id}"))[1]["slides"] for e in s["elements"]}
        text = " ".join(WORDS[:n]) + "."
        for slide, e, own, floor in shrinkable:
            r = await p.op({"type": "set_text", "target": e["id"], "base_revision": current[e["id"]]["revision"], "payload": {"text": text}})
            assert r["status"] == "applied", r
        view, pend, judged = await export_and_judge(p, memo_id, folder / f"ladder_{n}.pptx")
        elements = {e["id"]: e for s in view["slides"] for e in s["elements"]}
        for slide, e, own, floor in shrinkable:
            got = classify(elements[e["id"]], judged[e["id"]], pend, text)
            ladder.append({"what": f"slide {slide} · {e.get('name') or e.get('role')}", "own": own, "floor": floor, "words": n, **got})
    return {"firm": firm, "cases": cases, "agree": agree, "compared": compared, "ladder": ladder}


async def export_and_judge(p: Person, memo_id: str, pptx: Path):
    """The memo as the API returns it, its "does not fit" pendings, and PowerPoint's own layout of its export."""
    view = (await p.aget(f"/memos/{memo_id}"))[1]
    pend = {x["element_id"] for x in (await p.aget(f"/memos/{memo_id}/pendings"))[1] if x["kind"] == "does_not_fit"}
    status, data = (await p.aget(f"/memos/{memo_id}/export"))
    pptx.write_bytes(data)
    return view, pend, await asyncio.to_thread(powerpoint_says, pptx)


def overflows(pp: dict, e: dict) -> bool:
    """PowerPoint runs the text past the box (a table: its rows grow past its frame)."""
    if pp["kind"] == "table":
        return pp["room"] > e["frame"]["h_in"] + 0.05
    return pp["bound"] > pp["room"] * 1.02 + 0.01


def classify(e: dict, pp: dict, pend: set, text: str) -> dict:
    """One written box, judged by PowerPoint: fits, shrunk to fit, reported, or cut silently."""
    fit = e["fit"]
    over = overflows(pp, e)
    drawn = pp["text"].replace("\r", " ").replace("\v", " ").lower()   # a role in capitals draws the words in capitals
    whole = all(word.lower() in drawn for word in text.split()[-3:])
    shrunk = bool(fit.get("size_pt")) and fit.get("scale", 1) < 1
    reported = e["id"] in pend and not fit["fits"]
    if over or not whole:
        outcome = "reported" if reported else "cut silently"
    elif reported:
        outcome = "reported (PowerPoint fits it)"
    elif shrunk:
        outcome = "shrunk to fit"
    else:
        outcome = "fits"
    return {"outcome": outcome, "whole": whole, "drawn_pt": pp["size"], "fit_pt": fit.get("size_pt"), "needs_in": fit.get("needed_in"),
            "room_in": round(pp["room"], 2) if pp["kind"] == "text" else e["frame"]["h_in"],
            "powerpoint_in": round(pp["bound"], 2), "pending": e["id"] in pend}


def report(results: list[dict]) -> str:
    outcomes = ["fits", "shrunk to fit", "reported", "reported (PowerPoint fits it)", "cut silently"]
    lines = [f"# Overflow · {datetime.now():%Y-%m-%d %H:%M}", "",
             "Measure 3: a long-content set we write (six lengths, the same words for every firm) into the boxes people and the",
             "model write, through the memo's websocket; the memo exported through the API; PowerPoint itself judges each box.", "",
             "| | " + " | ".join(r["firm"] for r in results) + " |", "|---|" + "---|" * len(results),
             "| Boxes written (and ladder steps) | " + " | ".join(f"{len(r['cases'])} ({len(r['ladder'])})" for r in results) + " |"]
    for o in outcomes:
        label = f"**{o}**" if o == "cut silently" else o
        lines.append(f"| {label} | " + " | ".join(str(sum(1 for c in r["cases"] + r["ladder"] if c["outcome"] == o)) for r in results) + " |")
    lines.append("| continued on another slide (not built) | " + " | ".join("0" for _ in results) + " |")
    lines.append("| Every box: the system's measure and PowerPoint agree on fits / does not fit | " + " | ".join(
        f"{r['agree']}/{r['compared']}" for r in results) + " |")
    lines += ["", "## Each box written", "",
              "| Firm | Box | Length | Outcome | Drawn at (pt) | Needs / room (in) | PowerPoint (in) | Pending |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        for c in r["cases"]:
            lines.append(f"| {r['firm']} | {c['what']} | {c['length']} | {c['outcome']} | {c['drawn_pt']:g}"
                         f"{' (shrunk from its own)' if c['outcome'] == 'shrunk to fit' else ''} | {c['needs_in']:.2f} / {c['room_in']:.2f} | "
                         f"{c['powerpoint_in']:.2f} | {'yes' if c['pending'] else '—'} |")
    lines += ["", "## The ladder: boxes that may get smaller, written longer and longer", "",
              "Each step: the same words in every such box, the memo exported, PowerPoint judging. A box should fit at its own size,",
              "then fit only smaller (never below the firm's minimum), then be reported; never be cut silently.", ""]
    for r in results:
        boxes = {}
        for c in r["ladder"]:
            boxes.setdefault(c["what"], []).append(c)
        for what, steps in boxes.items():
            seq = " → ".join(f"{c['words']}w {c['outcome']}" + (f" ({c['drawn_pt']:g} pt)" if c["outcome"] == "shrunk to fit" else "")
                             for c in steps)
            lines.append(f"- {r['firm']} · {what} ({steps[0]['own']:g} pt, minimum {steps[0]['floor']:g}): {seq}")
    broke = [f"- {r['firm']} · {c['what']} ({c['length']}): cut silently" for r in results for c in r["cases"] if c["outcome"] == "cut silently"]
    broke += [f"- {r['firm']} · {c['what']} ({c['words']} words, ladder): cut silently" for r in results for c in r["ladder"]
              if c["outcome"] == "cut silently"]
    lines += ["", "## Where it breaks", ""] + (broke or ["Nothing was cut silently."])
    lines += ["", "Not built: long content does not continue on another slide by the firm's rules yet; what does not fit even at",
              "the smallest size is reported (a pending, and marked on the canvas) for a person to shorten."]
    return "\n".join(lines) + "\n"


async def main_async(firms):
    out = []
    with Server(PORT) as server:
        for firm in firms:
            out.append(await run_firm(server, firm))
            sys.stdout.write(f"{firm}: done\n")
            sys.stdout.flush()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firm")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name)) if not args.firm or f.name == args.firm]
    results = asyncio.run(main_async(firms))
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_overflow.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
