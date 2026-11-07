"""The brief's suite of edits made in PowerPoint, in repeated round trips, on every firm.

Each round: export the memo as it is, make the edits in the file (python-pptx, as a person would in
PowerPoint), import it, export again and check what came back. The expected result of each edit comes from
the firm's rules (locks, section counts), never from the firm's name.

Edits (brief §05): drop an image, drag into a locked zone, duplicate a slide, type over a figure, paste a
chart as a picture; plus a free text edit, which must survive.

Reports, per firm:
  measure 1  edits that survive, conflicts surfaced rather than lost (every edit found has its operation;
             kept-aside edits have an open conflict; applied edits are in the next export)
  measure 2  how much of the memo survives PowerPoint and back (untouched elements identical, identity kept)
  where it breaks: every result that differs from the expected one, with its round

Usage (from the repo root):  backend/.venv/Scripts/python evals/powerpoint_suite.py [--rounds 5] [--firm f]
"""
from __future__ import annotations

import argparse
import copy
import io
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.history import Conflict  # noqa: E402
from app.db.memo import Element, ElementParagraph, Segment, Slide  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.generator.memo import Generator  # noqa: E402
from app.services.roundtrip import SHAPE, SLIDE, export, import_pptx, read  # noqa: E402

WORK = REPO / "backend" / ".work" / "evals" / "powerpoint"
STAGES = {}                                   # firm → stage used: the stage with the most required sections


def picture() -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (400, 260), "#777777").save(buf, "PNG")
    buf.seek(0)
    return buf


def duplicate(prs, slide) -> None:
    """Copy a slide right after itself, as PowerPoint's Duplicate Slide does (same shapes, same names)."""
    new = prs.slides.add_slide(slide.slide_layout)
    for ph in list(new.placeholders):
        ph._element.getparent().remove(ph._element)
    for shape in slide.shapes:
        new.shapes._spTree.append(copy.deepcopy(shape._element))
    new._element.cSld.set("name", slide._element.cSld.get("name"))
    ids = prs.slides._sldIdLst
    moved = ids[-1]
    ids.remove(moved)
    ids.insert(list(prs.slides).index(slide) + 1, moved)


class Facts:
    """What the database says about the memo's elements and slides (locks, chips, section counts)."""

    def __init__(self, memo_id):
        with SessionLocal() as s:
            rows = s.execute(select(Element, Slide).join(Slide, Element.slide_id == Slide.id)
                             .where(Slide.memo_id == memo_id, Element.deleted_at.is_(None))).all()
            self.element = {str(e.id): e for e, _ in rows}
            chips = s.execute(select(ElementParagraph.element_id).join(Segment, Segment.paragraph_id == ElementParagraph.id)
                              .where(Segment.kind == "chip", ElementParagraph.element_id.in_([e.id for e, _ in rows]))).scalars().all()
            self.has_chip = {str(x) for x in chips}
            sections = {x.id: x for x in s.scalars(select(S.Section))}
            self.slide_section = {str(sl.id): sections.get(sl.section_id) for _, sl in rows}
            self.conflicts = s.scalars(select(Conflict).where(Conflict.memo_id == memo_id)).all()


def make_edits(path: Path, out: Path, facts: Facts, round_no: int) -> list[dict]:
    """The suite's edits on an exported file. Each edit carries the operation and status expected."""
    prs = Presentation(str(path))
    edits, used = [], set()
    named = [(sl, sh, SHAPE.match(sh.name).group(1)) for sl in prs.slides for sh in sl.shapes if SHAPE.match(sh.name or "")]

    def pick(cond, skip_first=0):
        options = [(sl, sh, eid) for sl, sh, eid in named if eid not in used and eid in facts.element and cond(sl, sh, facts.element[eid])]
        if not options:
            return None
        choice = options[(round_no - 1 + skip_first) % len(options)]
        used.add(choice[2])
        return choice

    free = pick(lambda sl, sh, e: e.type == "text" and e.firm_lock == "content" and sh.has_text_frame
                and str(e.id) not in facts.has_chip and sh.text_frame.text.strip() and sh.text_frame.paragraphs[0].runs)
    if free:
        sl, sh, eid = free
        new = f"Edited in PowerPoint, round {round_no}"
        sh.text_frame.paragraphs[0].runs[0].text = new
        for p in sh.text_frame.paragraphs[1:]:
            for r in p.runs:
                r.text = ""
        edits.append({"edit": "free text", "element": eid, "op": "set_text", "expect": "applied", "survives": new})
    fig = pick(lambda sl, sh, e: sh.has_text_frame and str(e.id) in facts.has_chip and any(
        any(ch.isdigit() for ch in r.text) for p in sh.text_frame.paragraphs for r in p.runs))
    if fig:
        sl, sh, eid = fig
        run = next(r for p in sh.text_frame.paragraphs for r in p.runs if any(ch.isdigit() for ch in r.text))
        before = run.text
        run.text = "".join("9" if ch.isdigit() else ch for ch in before)
        edits.append({"edit": "type over a figure", "element": eid, "op": "set_text", "expect": "refused", "stays": before})
    drag = pick(lambda sl, sh, e: e.firm_lock in ("content", "fixed") and sh.width and sh.height)
    if drag:
        sl, sh, eid = drag
        sh.left = (sh.left or 0) + Inches(1.0)
        edits.append({"edit": "drag into a locked zone", "element": eid, "op": "set_frame", "expect": "refused"})
    slides = [sl for sl in prs.slides if SLIDE.match(sl._element.cSld.get("name") or "")]
    target = slides[min(1, len(slides) - 1)]
    target.shapes.add_picture(picture(), Inches(1), Inches(1), Inches(2))
    edits.append({"edit": "drop an image", "op": "add_element", "expect": "kept_aside"})
    chart_slide = next((sl for sl, sh, _ in named if getattr(sh, "has_chart", False)), None)
    if chart_slide is not None:
        chart_slide.shapes.add_picture(picture(), Inches(2), Inches(2), Inches(3))
        edits.append({"edit": "paste a chart as a picture", "op": "add_element", "expect": "kept_aside"})
    plain = [sl for sl in slides if not any(getattr(sh, "has_chart", False) or sh.shape_type == 13 for sh in sl.shapes)]
    if plain:
        sl = plain[(round_no - 1) % len(plain)]
        sid = SLIDE.match(sl._element.cSld.get("name")).group(1)
        section = facts.slide_section.get(sid)
        duplicate(prs, sl)
        allowed = section is None or section.count != "one"
        edits.append({"edit": "duplicate a slide", "slide": sid, "op": "duplicate_slide",
                      "expect": "applied" if allowed else "refused"})
    prs.save(str(out))
    return edits


def texts(path: Path) -> dict[str, dict]:
    """Element id → what the file shows for it (text, cells, chart data, frame)."""
    out = {}
    for sl in read(path)["slides"]:
        for eid, shape in sl["shapes"].items():
            out.setdefault(eid, {k: v for k, v in shape.items() if k != "revision"})
    return out


def run_firm(firm: str, rounds: int) -> dict:
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        version = s.scalar(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc()))
        stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == version.id)).all()
        stage = max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id,
                                                                                         S.StageSection.required)).all()))
        memo_id = Generator(s, firm, stage.name, "2026-10-15", "Lead (lead); Associate").run()["memo_id"]
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    totals = {"edits": 0, "as_expected": 0, "applied": 0, "refused": 0, "kept_aside": 0, "survived": 0, "should_survive": 0,
              "stayed": 0, "should_stay": 0, "lost": 0, "conflicts_missing": 0, "untouched": 0, "untouched_identical": 0,
              "identity_kept": 0, "identity_total": 0}
    breaks = []
    for n in range(1, rounds + 1):
        exported, edited, after = folder / f"r{n}_export.pptx", folder / f"r{n}_edited.pptx", folder / f"r{n}_after.pptx"
        export(memo_id, exported)
        facts = Facts(memo_id)
        conflicts_before = len(facts.conflicts)
        edits = make_edits(exported, edited, facts, n)
        result = import_pptx(memo_id, edited, actor_email=f"alex@{firm}.demo")
        export(memo_id, after)
        before_view, after_view = texts(exported), texts(after)
        got = list(result["results"])
        # every change found has an operation, so nothing is lost silently
        totals["lost"] += result["changes"] - len(got)
        for e in edits:
            totals["edits"] += 1
            match = next((g for g in got if g["type"] == e["op"] and (not e.get("element") or g.get("target") == e["element"])), None)
            if match is not None:
                got.remove(match)
            status = match["status"] if match else "missing"
            totals[status] = totals.get(status, 0) + 1
            if status == e["expect"]:
                totals["as_expected"] += 1
            else:
                breaks.append(f"round {n} · {e['edit']}: expected {e['expect']}, got {status}"
                              + (f" ({match['reason']})" if match and match.get("reason") else ""))
            if e.get("survives"):
                totals["should_survive"] += 1
                if after_view.get(e["element"], {}).get("text", "").lower().startswith(e["survives"].lower()):  # caps boxes paint upper case
                    totals["survived"] += 1
                else:
                    breaks.append(f"round {n} · {e['edit']}: applied edit not in the next export")
            if e.get("stays"):
                totals["should_stay"] += 1
                if e["stays"] in after_view.get(e["element"], {}).get("text", ""):
                    totals["stayed"] += 1
                else:
                    breaks.append(f"round {n} · {e['edit']}: the record figure changed")
        for g in got:                                      # operations nobody planned (a side effect of an edit)
            breaks.append(f"round {n} · unplanned operation {g['type']} → {g['status']}: {g.get('reason') or ''}")
        kept = sum(1 for g in result["results"] if g["status"] == "kept_aside")
        new_conflicts = len(Facts(memo_id).conflicts) - conflicts_before
        totals["conflicts_missing"] += max(0, kept - new_conflicts)
        touched = {e.get("element") for e in edits}
        for eid, view in before_view.items():
            totals["identity_total"] += 1
            if eid in after_view:
                totals["identity_kept"] += 1
            if eid in touched:
                continue
            totals["untouched"] += 1
            if after_view.get(eid) == view:
                totals["untouched_identical"] += 1
            else:
                breaks.append(f"round {n} · untouched element {eid[:8]} came back different")
    return {"firm": firm, "stage": stage.name, "rounds": rounds, **totals, "breaks": breaks}


def pct(a, b) -> str:
    return f"{100 * a / b:.0f}% ({a}/{b})" if b else "–"


def report(results: list[dict]) -> str:
    out = [f"# PowerPoint suite · {datetime.now():%Y-%m-%d %H:%M}", "",
           "The brief's edits made in PowerPoint (drop an image, drag into a locked zone, duplicate a slide, type over",
           "a figure, paste a chart as a picture; plus free text), imported back through the operations door, in",
           "repeated round trips. One person; two people editing come with real time.", "",
           "| | " + " | ".join(f"{r['firm']} ({r['stage']}, {r['rounds']} rounds)" for r in results) + " |",
           "|---|" + "---|" * len(results)]
    rows = [
        ("Edits made", lambda r: r["edits"]),
        ("Result as expected by the firm's rules", lambda r: pct(r["as_expected"], r["edits"])),
        ("Applied / refused / kept aside", lambda r: f"{r['applied']} / {r['refused']} / {r['kept_aside']}"),
        ("**Measure 1** · applied edits in the next export", lambda r: pct(r["survived"], r["should_survive"])),
        ("**Measure 1** · record figures unchanged after typing over", lambda r: pct(r["stayed"], r["should_stay"])),
        ("**Measure 1** · kept-aside edits with an open conflict", lambda r: "all" if r["conflicts_missing"] == 0 else f"{r['conflicts_missing']} missing"),
        ("**Measure 1** · changes found with no operation (lost)", lambda r: r["lost"]),
        ("**Measure 2** · untouched elements identical after the round trip", lambda r: pct(r["untouched_identical"], r["untouched"])),
        ("**Measure 2** · element identity kept", lambda r: pct(r["identity_kept"], r["identity_total"])),
    ]
    for label, f in rows:
        out.append(f"| {label} | " + " | ".join(str(f(r)) for r in results) + " |")
    out += ["", "## Where it breaks", ""]
    lines = [f"- {r['firm']} · {b}" for r in results for b in r["breaks"]]
    out += lines or ["Nothing broke."]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--firm")
    a = parser.parse_args()
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name))]
    results = [run_firm(f, a.rounds) for f in firms if not a.firm or f == a.firm]
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_powerpoint.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
