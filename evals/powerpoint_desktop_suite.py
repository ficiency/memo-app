"""The brief's PowerPoint edits made and saved by PowerPoint desktop itself, on every firm.

The brief (§02): "PowerPoint is an editor, not an exit: an exported file edited by a person comes back". The
PowerPoint suite edits the exported file with a script; this one has the program people use, Microsoft PowerPoint,
open the file, make the edits and save it, through its automation interface (COM, Windows), so what comes back is
a file PowerPoint wrote.

Per firm, on a new memo made through the API with a free slide added (as the rail does), exported through the API:
  edited in PowerPoint   a box's text rewritten (applied); a figure typed over (refused, figure unchanged); a box
                         the firm places moved (refused); the free slide duplicated (applied); a slide of a section
                         allowed once duplicated (refused); a picture inserted on the free slide (applied, a
                         picture to confirm); a picture inserted on a template slide (kept aside); a chart pasted
                         as a picture next to it (kept aside, when the memo has a chart)
  saved by PowerPoint    the file uploaded through the API (the Upload button)
  checked                each edit's result as the firm's rules say; every box nobody edited unchanged (text,
                         place, revision) after PowerPoint saved the file; every shape's identity kept

Needs PowerPoint (Windows). Usage (from the repo root):  backend/.venv/Scripts/python evals/powerpoint_desktop_suite.py [--firm f]
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import REPO, Person, Server, latest_spec_and_record  # noqa: E402

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

PORT = 8016
WORK = REPO / "backend" / ".work" / "evals" / "powerpoint_desktop"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TOL = 0.02

# PowerPoint desktop does what the plan says, then saves the file itself
EDIT = r"""
param($file, $plan)
$p = Get-Content $plan -Raw | ConvertFrom-Json
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($file, $false, $false, $false)
function Shape($slide, $name) { foreach ($sh in $pres.Slides.Item($slide).Shapes) { if ($sh.Name -eq $name) { return $sh } }; return $null }
$done = @()
if ($p.text) { $sh = Shape $p.text.slide $p.text.name; $sh.TextFrame.TextRange.Text = $p.text.value; $done += 'text' }
if ($p.figure) { $sh = Shape $p.figure.slide $p.figure.name; $tr = $sh.TextFrame.TextRange; $tr.Text = $tr.Text.Replace($p.figure.old, $p.figure.new); $done += 'figure' }
if ($p.move) { $sh = Shape $p.move.slide $p.move.name; $sh.Left = $sh.Left + 72; $sh.Top = $sh.Top + 36; $done += 'move' }
if ($p.picture_free) { $pres.Slides.Item($p.picture_free.slide).Shapes.AddPicture($p.picture_free.file, 0, -1, 200, 260, 160, 110) | Out-Null; $done += 'picture_free' }
if ($p.picture_template) { $pres.Slides.Item($p.picture_template.slide).Shapes.AddPicture($p.picture_template.file, 0, -1, 40, 40, 120, 80) | Out-Null; $done += 'picture_template' }
if ($p.chart_picture) {
  $sh = Shape $p.chart_picture.slide $p.chart_picture.name
  $sh.Chart.Export($p.chart_picture.file) | Out-Null
  $pres.Slides.Item($p.chart_picture.slide).Shapes.AddPicture($p.chart_picture.file, 0, -1, $sh.Left + 20, $sh.Top + 20, $sh.Width / 2, $sh.Height / 2) | Out-Null
  $done += 'chart_picture' }
if ($p.duplicate_free) { $pres.Slides.Item($p.duplicate_free.slide).Duplicate() | Out-Null; $done += 'duplicate_free' }
if ($p.duplicate_once) { $pres.Slides.Item($p.duplicate_once.slide).Duplicate() | Out-Null; $done += 'duplicate_once' }
$pres.Save(); $pres.Close(); $pp.Quit()
$done -join ','
"""


def picture(path: Path) -> Path:
    im = Image.new("RGB", (400, 280), (40, 120, 90))
    im.save(path)
    return path


def stage_for(firm: str) -> str:
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc())).first()
        stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id)).all()
        return max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id,
                                                                                    S.StageSection.required)).all())).name


def text_of(e: dict) -> str:
    return "\n".join("".join(x["text"] for x in p["segments"]) for p in e["paragraphs"])


def boxes(view: dict) -> dict:
    return {e["id"]: e for s in view["slides"] for e in s["elements"]}


def run_firm(server: Server, firm: str) -> dict:
    p = Person(server, f"alex@{firm}.demo")
    version, revision, _ = latest_spec_and_record(p)
    status, made = p.post("/memos", {"spec_version_id": version, "record_revision_id": revision, "stage": stage_for(firm),
                                     "memo_date": "2026-10-15", "deal_team": "Lead (lead); Associate"})
    assert status == 200, made
    memo_id = made["memo_id"]
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    log = []

    def record(what, expected, got, detail=""):
        log.append({"what": what, "expected": expected, "got": got, "detail": str(detail)[:200]})

    try:
        view = p.get(f"/memos/{memo_id}")[1]
        sections, zones = view["spec"]["sections"], view["spec"].get("zones") or {}
        free_layout = next((l for sec in sections.values() if sec["status"] == "free" for l in sec["layouts"] if l["id"] in zones), None)
        for position in range(len(view["slides"]), -1, -1):     # where the firm's order allows it, as a person does
            status, r = p.post(f"/memos/{memo_id}/operations", {"type": "add_slide", "target": memo_id,
                                                                "payload": {"layout_id": free_layout["id"], "position": position}})
            if r.get("status") == "applied" or "order" not in (r.get("reason") or ""):
                break
        view = p.get(f"/memos/{memo_id}")[1]
        before = boxes(view)
        status, data = p.get(f"/memos/{memo_id}/export")
        exported = folder / "export.pptx"
        exported.write_bytes(data)

        # the plan: which shapes PowerPoint edits, chosen from the memo by the firm's rules
        prs = Presentation(str(exported))
        index = {}                                          # slide id → 1-based slide number in the file
        for n, sl in enumerate(prs.slides, start=1):
            name = sl._element.cSld.get("name") or ""
            if name.startswith("memo-slide:"):
                index[name.split(":")[1]] = n
        shape_name = {e["id"]: f"memo:{e['id']}@{e['revision']}" for e in before.values()}
        slide_of = {e["id"]: index.get(s["id"]) for s in view["slides"] for e in s["elements"]}
        free_slide = next(s for s in view["slides"] if s["section_id"] and sections[s["section_id"]]["status"] == "free")
        template = [s for s in view["slides"] if s["section_id"] and sections[s["section_id"]]["status"] != "free"]
        once = next((s for s in template if sections[s["section_id"]]["count"] == "one"), None)

        def text_boxes(with_figure: bool):
            for s in template:
                for e in s["elements"]:
                    segs = [x for p_ in e["paragraphs"] for x in p_["segments"]]
                    has = any(x["kind"] == "chip" and any(c.isdigit() for c in x["text"]) and not x["text"].startswith("[no data") for x in segs)
                    if e["type"] == "text" and not e.get("master") and e["firm_lock"] == "content" and segs and has == with_figure \
                            and (with_figure or not any(x["kind"] == "chip" for x in segs)):
                        yield e

        free_text = next(text_boxes(False))
        figure_box = next(text_boxes(True), None)
        in_file = {sh.name for sl in prs.slides for sh in sl.shapes}
        movable = next(e for s_ in template[1:] + template[:1] for e in s_["elements"] if e["type"] == "text" and not e.get("master")
                       and e["firm_lock"] != "free" and shape_name[e["id"]] in in_file
                       and e["id"] not in (free_text["id"], (figure_box or {}).get("id")))
        chart = next((e for s in template for e in s["elements"] if e["type"] == "chart" and e.get("chart")), None)
        pic = picture(folder / "team.png")
        plan = {"text": {"slide": slide_of[free_text["id"]], "name": shape_name[free_text["id"]], "value": "Written in PowerPoint desktop"},
                "move": {"slide": slide_of[movable["id"]], "name": shape_name[movable["id"]]},
                "picture_free": {"slide": index[free_slide["id"]], "file": str(pic.resolve())},
                "picture_template": {"slide": index[template[0]["id"]], "file": str(pic.resolve())},
                "duplicate_free": {"slide": index[free_slide["id"]]}}
        chip = None
        if figure_box is not None:
            chip = next(x["text"] for p_ in figure_box["paragraphs"] for x in p_["segments"] if x["kind"] == "chip" and any(c.isdigit() for c in x["text"]))
            plan["figure"] = {"slide": slide_of[figure_box["id"]], "name": shape_name[figure_box["id"]], "old": chip,
                              "new": "".join("9" if c.isdigit() else c for c in chip)}
        if once is not None:
            plan["duplicate_once"] = {"slide": index[once["id"]]}
        if chart is not None:
            plan["chart_picture"] = {"slide": slide_of[chart["id"]], "name": shape_name[chart["id"]], "file": str((folder / "chart.png").resolve())}
        edited = folder / "edited_in_powerpoint.pptx"
        edited.write_bytes(exported.read_bytes())
        (folder / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        script = WORK / "edit.ps1"
        script.write_text(EDIT, encoding="utf-8")
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(edited.resolve()),
                               str((folder / "plan.json").resolve())], capture_output=True, text=True, timeout=600)
        record("PowerPoint desktop made the edits and saved the file", "saved", "saved" if done.returncode == 0 else "failed",
               (done.stdout.strip() or done.stderr.strip())[:200])
        names_kept = {sh.name for sl in Presentation(str(edited)).slides for sh in sl.shapes if sh.name.startswith("memo:")}
        kept = sum(1 for eid in before if shape_name[eid] in names_kept)
        painted = sum(1 for eid in before if shape_name[eid] in {sh.name for sl in prs.slides for sh in sl.shapes})

        status, back = p.upload(f"/memos/{memo_id}/upload", edited.name, edited.read_bytes(), PPTX_TYPE)
        record("the file PowerPoint saved comes back as this memo's", "bring_back", back.get("kind") if isinstance(back, dict) else status,
               (back.get("detail") if isinstance(back, dict) else "") or f"HTTP {status}")
        results = back.get("results", []) if isinstance(back, dict) else []
        by = {}
        for x in results:
            by.setdefault((x["type"], x["target"]), []).append(x["status"])

        def got(kind, target):
            return (by.get((kind, target)) or ["missing"])[0]

        after = boxes(p.get(f"/memos/{memo_id}")[1])
        record("a box's text rewritten", "applied", got("set_text", free_text["id"]))
        record("the new text is the memo's", "yes", "yes" if text_of(after[free_text["id"]]).startswith("Written in PowerPoint desktop") else "no")
        if figure_box is not None:
            record("a figure typed over", "refused", got("set_text", figure_box["id"]))
            record("the figure unchanged", "yes", "yes" if chip in text_of(after[figure_box["id"]]) else "no")
        record("a box the firm places moved", "refused", got("set_frame", movable["id"]))
        record("the free slide duplicated", "applied", got("duplicate_slide", free_slide["id"]))
        if once is not None:
            record(f"a slide allowed once duplicated ({sections[once['section_id']]['name']})", "refused", got("duplicate_slide", once["id"]))
        adds = [x for x in results if x["type"] == "add_element"]
        on_free = [x["status"] for x in adds if x["target"] == free_slide["id"]]
        record("a picture inserted on the free slide", "applied", on_free[0] if on_free else "missing")
        pend = [x for x in p.get(f"/memos/{memo_id}/pendings")[1] if x["kind"] == "picture" and x["slide_id"] == free_slide["id"]]
        record("that picture is a pending to confirm", "yes", "yes" if pend else "no")
        on_template = [x["status"] for x in adds if x["target"] == template[0]["id"]]
        record("a picture inserted on a template slide", "kept_aside", on_template[0] if on_template else "missing")
        if chart is not None:
            on_chart_slide = [x["status"] for x in adds if x["target"] == next(s["id"] for s in view["slides"] if any(e["id"] == chart["id"] for e in s["elements"]))]
            record("a chart pasted as a picture", "kept_aside", on_chart_slide[-1] if on_chart_slide else "missing")

        # everything nobody edited: unchanged after PowerPoint saved the file
        edited_ids = {free_text["id"], movable["id"]} | ({figure_box["id"]} if figure_box else set())
        untouched = [eid for eid in before if eid not in edited_ids and not before[eid].get("master")]
        same = 0
        changed = []
        for eid in untouched:
            a, b = before[eid], after.get(eid)
            ok = b is not None and b["revision"] == a["revision"] and text_of(b) == text_of(a) and \
                all(abs(a["frame"][k] - b["frame"][k]) <= TOL for k in ("x_in", "y_in", "w_in", "h_in"))
            same += ok
            if not ok:
                changed.append(f"{a.get('name') or a.get('role') or a['type']} ({eid[:8]})")
        record("boxes nobody edited unchanged after PowerPoint saved the file", f"{len(untouched)}/{len(untouched)}",
               f"{same}/{len(untouched)}", ", ".join(changed[:6]))
        record("every shape keeps its identity in the saved file", f"{painted}/{painted}", f"{kept}/{painted}")
        return {"firm": firm, "log": log, "untouched": (same, len(untouched)), "identity": (kept, painted)}
    finally:
        import uuid as _uuid
        from sqlalchemy import delete
        from app.db.memo import Memo
        with SessionLocal() as s:
            s.execute(delete(Memo).where(Memo.id == _uuid.UUID(memo_id)))
            s.commit()


def report(results: list[dict]) -> str:
    lines = [f"# PowerPoint desktop · {datetime.now():%Y-%m-%d %H:%M}", "",
             "The brief's PowerPoint edits made and saved by Microsoft PowerPoint itself (through its automation interface), the",
             "file uploaded back through the API, on a new memo per firm.", "",
             "| | " + " | ".join(r["firm"] for r in results) + " |", "|---|" + "---|" * len(results)]
    ok = lambda r: f"{sum(1 for x in r['log'] if x['got'] == x['expected'])}/{len(r['log'])}"
    lines.append("| Checks as expected | " + " | ".join(ok(r) for r in results) + " |")
    lines.append("| Boxes nobody edited unchanged after PowerPoint saved the file | " + " | ".join(f"{r['untouched'][0]}/{r['untouched'][1]}" for r in results) + " |")
    lines.append("| Shapes whose identity PowerPoint kept | " + " | ".join(f"{r['identity'][0]}/{r['identity'][1]}" for r in results) + " |")
    lines += ["", "## Each check", ""]
    for r in results:
        lines.append(f"**{r['firm']}**")
        lines += [f"- {'✓' if x['got'] == x['expected'] else '✗'} {x['what']}: {x['got']}" + (f" — *{x['detail']}*" if x["detail"] else "") for x in r["log"]]
        lines.append("")
    broke = [f"- {r['firm']} · {x['what']}: expected {x['expected']}, got {x['got']}" + (f" ({x['detail']})" if x["detail"] else "")
             for r in results for x in r["log"] if x["got"] != x["expected"]]
    lines += ["## Where it breaks", ""] + (broke or ["Nothing broke."])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firm")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name)) if not args.firm or f.name == args.firm]
    with Server(PORT) as server:
        results = [run_firm(server, f) for f in firms]
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_powerpoint_desktop.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
