"""The agent: the brief's edits asked in words, and measure 4 (AI edits that leave the memo invalid or its linked
figures inconsistent), on a new memo per firm.

The brief (§05): "Build a suite of the edits real people make on the canvas, through the agent and in
PowerPoint ... Report, for each firm: ... AI edits that leave the memo invalid or its linked figures
inconsistent". Per firm:

  A  the brief's edits asked of the agent: change a figure, move a template box into the header, duplicate a
     slide the firm allows once and one it allows several times (added by the agent first), add an image;
     each is expected to be done or declined as the firm's rules say (an image: not supported yet)
  B  writing requests (fill a slide's slots, write a title, shorten a slide); every proposal is applied; then
     code checks the memo: amounts typed instead of figures, figures that do not match the record (each chip
     against the record through the binding), one concept shown with two values, refusals when applying,
     figures the firm's recipe expects that were removed
  C  the memo with the agent's edits through PowerPoint and back: nothing found changed
  D  custom slides through the agent (the brief: "ask the agent, which builds it from the firm's components with
     figures bound to the record"; "add a customer concentration slide"): a slide added with a record table or
     chart on it in one request, a chart added on it, and a table asked for on a template slide (declined); the
     points of the agent's charts are checked against the record too

The model is called: each request is one call (more when code sends errors back). The memos are temporary.

Usage (from the repo root):  backend/.venv/Scripts/python evals/agent_suite.py [--firm f]
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.history import Operation  # noqa: E402
from app.db.memo import Memo  # noqa: E402
from app.db.record import RecordRevision  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import proposals  # noqa: E402
from app.services.agent import slide as agent  # noqa: E402
from app.services.records import Record  # noqa: E402
from app.services.generator.memo import Generator  # noqa: E402
from app.services.memo_data import MemoData  # noqa: E402
from app.services.roundtrip import export, import_pptx  # noqa: E402

WORK = REPO / "backend" / ".work" / "evals" / "agent"


def stage_for(s, firm) -> str:
    f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
    v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc())).first()
    stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id)).all()
    return max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id, S.StageSection.required)).all())).name


class Run:
    def __init__(self, firm: str):
        self.firm = firm
        self.log: list[dict] = []
        self.calls = self.tokens_in = self.tokens_out = 0
        self.seconds = 0.0
        with SessionLocal() as s:
            self.user = s.scalar(select(S.User).where(S.User.email == f"alex@{firm}.demo"))
            self.memo_id = Generator(s, firm, stage_for(s, firm), "2026-10-15", "Lead (lead); Associate").run()["memo_id"]

    def slides(self):
        with SessionLocal() as s:
            return MemoData(s, self.memo_id)

    def ask(self, request: str, slide_id, apply: bool = True) -> dict:
        r = agent.ask(self.memo_id, self.user, request, str(slide_id), None)
        self.calls += r["attempts"]
        self.tokens_in += r["tokens"][0]
        self.tokens_out += r["tokens"][1]
        self.seconds += r["seconds"]
        applied = []
        if apply and r["proposal"]:
            with SessionLocal() as s:
                out, _, _ = proposals.apply(s, r["proposal"]["id"], self.user.id)
            applied = [i["result"] for i in out["operations"]]
        r["applied"] = applied
        return r

    def record(self, part, what, expected, got, detail=""):
        self.log.append({"part": part, "what": what, "expected": expected, "got": got, "detail": detail})

    def close(self):
        with SessionLocal() as s:
            s.execute(delete(Memo).where(Memo.id == uuid.UUID(self.memo_id)))
            s.commit()


def proposed_types(r) -> list[str]:
    return [i["op"]["type"] for i in (r["proposal"] or {}).get("operations", [])]


def part_a(run: Run) -> None:
    d = run.slides()
    with SessionLocal() as s:
        secs = {x.id: x for x in s.scalars(select(S.Section).where(S.Section.spec_version_id == d.version.id))}
        links = defaultdict(list)
        for link in s.scalars(select(S.SectionLayout).where(S.SectionLayout.section_id.in_(list(secs)))):
            links[link.section_id].append(link.layout_id)
    # a figure in a box, and a template box on the same slide
    target = None
    for sl in d.slides:
        for e in d.elements_of.get(sl.id, []):
            chips = [x for _, ss in d.paragraphs(element_id=e.id) for x in ss if x.kind == "chip" and not x.text.startswith("[no data")]
            if chips and e.type == "text":
                target = (sl, e, chips[0])
                break
        if target:
            break
    sl, el, chip = target
    r = run.ask(f"Change {chip.concept_name.replace('_', ' ')} to 999.", sl.id, apply=False)
    run.record("A", "change a figure (\"change … to 999\")", "declined", "declined" if not r["proposal"] else f"proposed {proposed_types(r)}", r["reply"][:140])
    movable = next((e for e in d.elements_of.get(sl.id, []) if e.type == "text" and e.firm_lock == "content" and e.id != el.id), el)
    r = run.ask(f"Move the box '{movable.name or movable.role}' up into the header area.", sl.id, apply=False)
    run.record("A", "move a template box into the header", "declined",
               "declined" if "set_frame" not in proposed_types(r) else "proposed a move", r["reply"][:140])
    once = next((x for x in d.slides if secs.get(x.section_id) is not None and secs[x.section_id].count == "one"), None)
    if once is not None:
        r = run.ask("Duplicate this slide.", once.id, apply=False)
        run.record("A", f"duplicate a slide the firm allows once ({secs[once.section_id].name})", "declined",
                   "declined" if "duplicate_slide" not in proposed_types(r) else "proposed a duplicate", r["reply"][:140])
    many = next((x for x in secs.values() if x.count != "one" and links.get(x.id)), None)
    if many is not None:
        r = run.ask(f"Add a {many.name} slide.", d.slides[-1].id)
        added = [a for a in r["applied"] if a["status"] == "applied"]
        run.record("A", f"add a slide of a section allowed several times ({many.name})", "applied",
                   "applied" if added else ("declined" if not r["proposal"] else "refused"), r["reply"][:140])
        if added:
            d = run.slides()
            new = next((x for x in d.slides if x.section_id == many.id), None)
            r = run.ask("Duplicate this slide.", new.id)
            ok = any(a["status"] == "applied" for a in r["applied"])
            run.record("A", f"duplicate a slide the firm allows several times ({many.name})", "applied",
                       "applied" if ok else ("declined" if not r["proposal"] else "refused"), r["reply"][:140])
    r = run.ask("Add this image of the team.", d.slides[0].id, apply=False)
    images = [i for i in (r["proposal"] or {}).get("operations", []) if i["op"]["type"] == "add_element"]
    run.record("A", "add an image (the agent cannot place one until step 9)", "no image placed",
               "no image placed" if not images else "placed an image", r["reply"][:140])


def part_b(run: Run) -> None:
    d = run.slides()
    with_slots = [sl for sl in d.slides if any(x.kind == "model_draft" for e in d.elements_of.get(sl.id, [])
                                               for _, ss in d.paragraphs(element_id=e.id) for x in ss)]
    requests = [("Fill the boxes the model should write on this slide.", sl) for sl in with_slots[:3]]
    if len(with_slots) > 3:
        requests.append(("Write the title as a one-line conclusion.", with_slots[3]))
    if with_slots:
        requests.append(("Shorten the text of this slide.", with_slots[0]))
    for req, sl in requests:
        r = run.ask(req, sl.id)
        statuses = [a["status"] for a in r["applied"]]
        run.record("B", f"slide {sl.position + 1}: {req}", "edits applied",
                   f"{statuses.count('applied')} applied" + (f", {len(statuses) - statuses.count('applied')} not" if len(statuses) != statuses.count('applied') else "")
                   if statuses else "no edit", r["reply"][:140])


def part_d(run: Run) -> None:
    d = run.slides()
    before = {x.id for x in d.slides}
    boxes_before = {e.id for e in d.elements}
    with SessionLocal() as ss:
        free_sections = {x.id for x in ss.scalars(select(S.Section).where(S.Section.spec_version_id == d.version.id, S.Section.status == "free"))}
    # a slide the firm lays out (not of a section it leaves free), there from the start
    template = next((x for x in d.slides if x.section_id not in free_sections and x.kind != "free"), d.slides[0])
    r = run.ask("Add a customer concentration slide: a table or chart of the customers from the deal record and a one-line "
                "takeaway.", d.slides[-1].id)
    d = run.slides()
    new = [x for x in d.slides if x.id not in before]
    # a record table or chart added: on a new slide, or on the free slide asked about
    boxes = [e for e in d.elements if e.id not in boxes_before and e.firm_lock == "free" and e.type in ("table", "chart")]
    run.record("D", "\"add a customer concentration slide\": a record table or chart on a slide for it", "applied",
               "applied" if boxes else ("declined" if not r["proposal"] else f"applied {len(new)} slide(s), no table or chart"),
               r["reply"][:140])
    target = new[0] if new else (next((x for x in d.slides if any(e.id in {b.id for b in boxes} for e in d.elements_of.get(x.id, []))), None))
    if target is not None:
        r = run.ask("Add a chart of revenue over the last periods on this slide.", target.id)
        charts = [a for a, i in zip(r["applied"], (r["proposal"] or {}).get("operations", []))
                  if a["status"] == "applied" and i["op"]["type"] == "add_element" and i["op"]["payload"].get("type") == "chart"]
        run.record("D", "add a chart of revenue on the new slide", "applied",
                   "applied" if charts else ("declined" if not r["proposal"] else "no chart applied"), r["reply"][:140])
    r = run.ask("Add a table of the P&L on this slide.", template.id, apply=False)
    adds = [i for i in (r["proposal"] or {}).get("operations", []) if i["op"]["type"] == "add_element"
            and i["op"]["payload"].get("slide_id") == str(template.id)]
    run.record("D", "add a table on a template slide", "declined", "declined" if not adds else "proposed a table there", r["reply"][:140])


def checks(run: Run) -> dict:
    """The memo after the agent, by value (a figure may be written differently in a table and in prose, and a
    concept that is a row of a table has one value per period or case):
      - every chip holds the record's value at its own place (its path, or its formula);
      - one concept at one place never holds two values;
      - no amount typed as text in the boxes the agent edited."""
    with SessionLocal() as s:
        d = MemoData(s, run.memo_id)
        memo = d.memo
        record = Record(s, memo.record_revision_id)
        ops = s.scalars(select(Operation).where(Operation.memo_id == memo.id, Operation.actor_type == "agent")).all()
    touched = {o.target_id for o in ops if o.status == "applied"}
    typed, wrong, values, by_agent = [], [], defaultdict(set), 0
    for e in d.elements:
        paras = d.paragraphs(element_id=e.id) + [p for t in [d.table_of.get(e.id)] if t for c in d.cells_of.get(t.id, []) for p in d.paragraphs(cell_id=c.id)]
        for _, ss in paras:
            for x in ss:
                if x.kind == "chip" and x.record_path and x.value_snapshot not in (None, ""):
                    row = record.resolve(x.record_path)
                    now = (row.number_value if row.number_value is not None else row.text_value) if row is not None                         else record.evaluate(x.record_path)
                    same = now is not None and (str(now) == x.value_snapshot or _close(now, x.value_snapshot))
                    if not same:
                        wrong.append(f"{x.concept_name} at {x.record_path}: holds {x.value_snapshot}, the record has {now}")
                    values[(x.concept_name, x.record_path)].add(x.value_snapshot)
                    by_agent += e.id in touched
                if x.kind == "text" and e.id in touched:
                    typed += [m.group(0) for m in agent.TYPED_FIGURE.finditer(x.text or "")]
        chart = d.chart_of.get(e.id)
        if chart is not None and chart.chart_template_id is None:
            for series in d.series_of.get(chart.id, []):
                periods = {str(r.period): r.number_value for r in record.families.get(series.record_path or "", [])}
                for pt in d.points_of.get(series.id, []):
                    have = [v for k, v in periods.items() if k.startswith(pt.category) or pt.category.startswith(k)]
                    if pt.value is not None and not any(_close(v, pt.value) for v in have):
                        wrong.append(f"chart {series.name}: {pt.category} holds {pt.value}, not the record's")
                    values[("chart " + (series.record_path or ""), pt.category)].add(str(pt.value))
    two_values = {f"{c} at {p}": v for (c, p), v in values.items() if len(v) > 1}
    warnings = [w for o in ops for w in (o.payload or {}).get("warnings", [])]
    refused = sum(1 for o in ops if o.status != "applied")
    return {"agent_edits": sum(1 for o in ops if o.status == "applied"), "refused_on_apply": refused, "typed": typed,
            "wrong": wrong, "two_values": two_values, "warnings": warnings, "touched": len(touched),
            "figures_checked": sum(len(v) for v in values.values()), "figures_in_agent_boxes": by_agent}


def _close(now, snapshot) -> bool:
    try:
        return abs(float(now) - float(snapshot)) < 1e-6
    except (TypeError, ValueError):
        return False


def part_c(run: Run) -> dict:
    out = WORK / run.firm
    out.mkdir(parents=True, exist_ok=True)
    exported = out / "with_agent_edits.pptx"
    export(run.memo_id, exported)
    result = import_pptx(run.memo_id, exported)
    return {"changes": result["changes"]}


def report(results: list[dict]) -> str:
    lines = [f"# The agent · {datetime.now():%Y-%m-%d %H:%M}", "",
             "The brief's edits asked of the agent in words, and measure 4: AI edits that leave the memo invalid or its",
             "linked figures inconsistent. A new memo per firm; every proposal of part B is applied before the checks.", "",
             "| | " + " | ".join(r["firm"] for r in results) + " |", "|---|" + "---|" * len(results)]

    def row(label, f):
        lines.append(f"| {label} | " + " | ".join(f(r) for r in results) + " |")

    def a_ok(r):
        a = [x for x in r["log"] if x["part"] == "A"]
        return f"{sum(1 for x in a if x['got'] == x['expected'])}/{len(a)}"
    row("A · the brief's edits, done or declined as the firm's rules say", a_ok)
    row("B · writing requests / agent edits applied", lambda r: f"{sum(1 for x in r['log'] if x['part'] == 'B')} / {r['checks']['agent_edits']}")
    row("**Measure 4** · edits refused when applied", lambda r: str(r["checks"]["refused_on_apply"]))
    row("**Measure 4** · amounts typed instead of figures", lambda r: str(len(r["checks"]["typed"])))
    row("Distinct figures checked against the record (chips in the boxes the agent edited)",
        lambda r: f"{r['checks']['figures_checked']} ({r['checks']['figures_in_agent_boxes']})")
    row("**Measure 4** · figures that do not hold the record's value", lambda r: str(len(r["checks"]["wrong"])))
    row("**Measure 4** · one figure holding two values", lambda r: str(len(r["checks"]["two_values"])))
    row("Recipe figures removed (warned, pending)", lambda r: str(len(r["checks"]["warnings"])))
    row("C · changes found after PowerPoint and back", lambda r: str(r["roundtrip"]["changes"]))

    def d_ok(r):
        x = [y for y in r["log"] if y["part"] == "D"]
        return f"{sum(1 for y in x if y['got'] == y['expected'])}/{len(x)}"
    row("D · custom slides through the agent (table or chart from the record)", d_ok)
    row("Model calls · tokens in / out", lambda r: f"{r['calls']} · {r['tokens_in']:,} / {r['tokens_out']:,}")
    row("Cost (USD) · time (s)", lambda r: f"${r['tokens_in'] * 5e-6 + r['tokens_out'] * 30e-6:.2f} · {r['seconds']:.0f}")
    lines += ["", "## Each request", ""]
    for r in results:
        lines.append(f"**{r['firm']}**")
        for x in r["log"]:
            mark = "✓" if x["got"] == x["expected"] or (x["part"] == "B" and x["got"] != "no edit") else "✗"
            lines.append(f"- {mark} {x['part']} · {x['what']}: {x['got']} — *{x['detail']}*")
        lines.append("")
    lines += ["## Where it breaks", ""]
    broke = []
    for r in results:
        c = r["checks"]
        broke += [f"- {r['firm']} · typed amount in an agent edit: {t}" for t in c["typed"]]
        broke += [f"- {r['firm']} · {w}" for w in c["wrong"]]
        broke += [f"- {r['firm']} · {k} shown as {sorted(v)}" for k, v in c["two_values"].items()]
        broke += [f"- {r['firm']} · {x['part']} · {x['what']}: expected {x['expected']}, got {x['got']}" for x in r["log"]
                  if x["part"] in ("A", "D") and x["got"] != x["expected"]]
        broke += [f"- {r['firm']} · C · {r['roundtrip']['changes']} change(s) found after PowerPoint and back"] if r["roundtrip"]["changes"] else []
    lines += broke or ["Nothing broke."]
    lines += ["", "## Not covered yet", "",
              "- Images (\"add this image\"): the agent cannot place one until the canvas has images (step 9).",
              "- The agent inside the two-people rounds: its edits here are applied one request at a time on one memo.",
              "- Overflow of what the agent writes: measured with real fonts in step 8."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firm")
    args = parser.parse_args()
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name)) if not args.firm or f.name == args.firm]
    results = []
    for firm in firms:
        run = Run(firm)
        try:
            part_a(run)
            part_b(run)
            part_d(run)
            c = checks(run)
            rt = part_c(run)
            results.append({"firm": firm, "log": run.log, "checks": c, "roundtrip": rt, "calls": run.calls,
                            "tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "seconds": run.seconds})
            sys.stdout.write(f"{firm}: done ({run.calls} calls)\n")
        finally:
            run.close()
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_agent.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
