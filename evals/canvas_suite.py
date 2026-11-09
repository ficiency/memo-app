"""The brief's edits made on the canvas, and measure 2 on a canvas slide, on every firm.

The brief (§05): "Build a suite of the edits real people make on the canvas ...: drop an image, drag into a
locked zone, duplicate a slide, type over a figure, paste a chart as a picture" and "report ... how much of a
canvas slide survives PowerPoint and back".

Everything is done the way the canvas does it, against a server of its own: operations over the memo's websocket,
pictures and files over the API. The expected result of each edit comes from the firm's rules (which sections it
leaves free, which it allows once or several times, where its free areas are), never from its name, and each
result is checked in what the memo then is (the memo as the API returns it, its pendings, the PowerPoint it
exports), not only in what the door answered.

Per firm, on a new memo:
  A  the five edits (each with its control: the same move where it is allowed must work)
     drop an image        on a free slide: placed, flagged as a pending, its bytes served back;
                          on a template slide: refused, nothing added
     drag into a locked   a box moved into the header: refused, the box where it was; a template box moved:
     zone                 refused; a box moved inside the free area: applied, snapped
     duplicate a slide    a section allowed several times: applied, the copy has the same boxes and words;
                          a section allowed once: refused, the slide count unchanged
     type over a figure   a figure in text replaced by a number: refused, the text unchanged; a figure in a table
                          cell: refused; words written around a figure: applied, the figure the same
     paste a chart as a   a chart's picture on a free slide: placed but never taken as a chart: a pending until
     picture              someone confirms it, a picture (not a chart) in the PowerPoint
  B  measure 2: a canvas slide with text (a figure in it), a record table, a record chart and a picture, out to
     PowerPoint (the API's export) and back (the API's upload): per kind of element, how many come back as the
     same element: same identity, kind, place, content, style, and their picture's bytes. Then a person moves
     the picture and rewrites the text in the file: both come back applied.

Usage (from the repo root):  backend/.venv/Scripts/python evals/canvas_suite.py [--firm f]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import REPO, Person, Server, latest_spec_and_record  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

PORT = 8012
WORK = REPO / "backend" / ".work" / "evals" / "canvas"
TOL = 0.02                                   # inches: what a PowerPoint round trip may move a box by


def photo() -> bytes:
    im = Image.new("RGB", (600, 400), (70, 110, 160))
    ImageDraw.Draw(im).ellipse((180, 80, 420, 320), fill=(230, 200, 120))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def chart_picture() -> bytes:
    """A bar chart as a picture (what someone pastes from Excel or another deck)."""
    im = Image.new("RGB", (640, 400), "white")
    d = ImageDraw.Draw(im)
    d.line((60, 20, 60, 360), fill="black", width=2)
    d.line((60, 360, 620, 360), fill="black", width=2)
    for i, h in enumerate((120, 180, 240, 300)):
        d.rectangle((100 + i * 130, 360 - h, 180 + i * 130, 360), fill=(60, 40, 90))
        d.text((110 + i * 130, 365), f"FY{23 + i}", fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def stage_for(firm: str) -> str:
    """The stage with the most required sections (the fullest memo the firm makes)."""
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc())).first()
        stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id)).all()
        return max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id,
                                                                                    S.StageSection.required)).all())).name


class Run:
    def __init__(self, firm: str, person: Person):
        self.firm, self.p = firm, person
        self.log: list[dict] = []
        self.measure2: dict = {}

    def record(self, part, what, expected, got, detail=""):
        self.log.append({"part": part, "what": what, "expected": expected, "got": got, "detail": str(detail)[:200]})

    def memo(self) -> dict:
        status, view = self.p.get(f"/memos/{self.memo_id}")
        assert status == 200, view
        return view

    def element(self, eid: str) -> dict | None:
        for s in self.memo()["slides"]:
            for e in s["elements"]:
                if e["id"] == eid:
                    return e
        return None

    def pendings(self) -> list[dict]:
        return self.p.get(f"/memos/{self.memo_id}/pendings")[1]


def text_of(e: dict) -> str:
    return "\n".join("".join(x["text"] for x in p["segments"]) for p in e["paragraphs"])


def frame(e: dict) -> tuple:
    return tuple(round(e["frame"][k], 3) for k in ("x_in", "y_in", "w_in", "h_in"))


async def part_a(run: Run) -> None:
    p, view = run.p, run.memo()
    spec = view["spec"]
    sections, zones = spec["sections"], spec.get("zones") or {}
    free_layout = next((l for sid, sec in sections.items() if sec["status"] == "free" for l in sec["layouts"] if l["id"] in zones), None)
    if free_layout is None:
        run.record("A", "a slide the firm leaves free", "exists", "none in the spec")
        return
    zone = zones[free_layout["id"]]
    zx, zy, zw, zh = zone
    # where the firm's order allows it: at the end, else further up (as a person does when the door says why)
    for position in range(len(view["slides"]), -1, -1):
        r = await p.op({"type": "add_slide", "target": run.memo_id, "payload": {"layout_id": free_layout["id"], "position": position}})
        if r["status"] == "applied" or "order" not in (r.get("reason") or ""):
            break
        run.record("A", f"add a free slide at {position + 1} (the firm's order decides)", "refused when the order says so", "refused when the order says so",
                   r.get("reason"))
    run.record("A", "add a slide the firm leaves free (the rail)", "applied", r["status"], r.get("reason"))
    if r["status"] != "applied":
        return                                           # the rest happens on that slide
    view = run.memo()
    free = next(s for s in view["slides"] if s["id"] == (r.get("created") or [None])[0]) if r.get("created") else view["slides"][position]
    template = next(s for s in view["slides"] if s["section_id"] and sections[s["section_id"]]["status"] != "free" and s["kind"] != "free")

    # 1. drop an image ----------------------------------------------------------------------------------------
    status, up = p.upload(f"/memos/{run.memo_id}/pictures", "team.png", photo(), "image/png")
    pic_payload = {"type": "image", "asset_id": up.get("asset_id"), "alt": "team.png", "x_in": zx, "y_in": zy, "w_in": 3.0, "h_in": 2.0}
    r = await p.op({"type": "add_element", "target": free["id"], "payload": {"slide_id": free["id"], **pic_payload}})
    pic = (r.get("created") or [None])[0]
    pend = [x for x in run.pendings() if x["element_id"] == pic and x["kind"] == "picture"]
    status_img, data = p.get(f"/elements/{pic}/image") if pic else (0, b"")
    same = hashlib.sha256(data if isinstance(data, bytes) else b"").hexdigest() == hashlib.sha256(photo()).hexdigest()
    ok = r["status"] == "applied" and len(pend) == 1 and status_img == 200 and same
    run.record("A", "drop an image on a free slide", "placed, flagged, served back",
               "placed, flagged, served back" if ok else f"{r['status']} · pending {len(pend)} · image {status_img} same={same}", r.get("reason"))
    if pic is None:
        return                                           # the moves and the round trip need that picture
    n_before = len(template["elements"])
    r = await p.op({"type": "add_element", "target": template["id"], "payload": {"slide_id": template["id"], **pic_payload}})
    after = next(s for s in run.memo()["slides"] if s["id"] == template["id"])
    run.record("A", "drop an image on a template slide", "refused, nothing added",
               "refused, nothing added" if r["status"] == "refused" and len(after["elements"]) == n_before else f"{r['status']} · boxes {n_before}→{len(after['elements'])}",
               r.get("reason"))

    # 2. drag into a locked zone ----------------------------------------------------------------------------------
    before = run.element(pic)
    r = await p.op({"type": "set_frame", "target": pic, "base_revision": before["revision"],
                    "payload": {"x_in": zx, "y_in": 0.1, "w_in": 3.0, "h_in": 2.0}})
    now = run.element(pic)
    run.record("A", "drag a box into the header", "refused, the box where it was",
               "refused, the box where it was" if r["status"] == "refused" and frame(now) == frame(before) else f"{r['status']} · {frame(before)}→{frame(now)}",
               r.get("reason"))
    locked = next(e for e in template["elements"] if e["type"] == "text" and e["firm_lock"] in ("content", "fixed") and not e.get("master"))
    r = await p.op({"type": "set_frame", "target": locked["id"], "base_revision": locked["revision"],
                    "payload": {"x_in": locked["frame"]["x_in"] + 1, "y_in": locked["frame"]["y_in"]}})
    run.record("A", "drag a template box", "refused", r["status"], r.get("reason"))
    target = (round(zx + 1.234, 3), round(zy + 0.567, 3))
    r = await p.op({"type": "set_frame", "target": pic, "base_revision": now["revision"],
                    "payload": {"x_in": target[0], "y_in": target[1], "w_in": 3.0, "h_in": 2.0}})
    moved = run.element(pic)
    snapped = all(abs(v * 10 - round(v * 10)) < 1e-6 for v in frame(moved)[:2])
    run.record("A", "drag a box inside the free area (control)", "applied, snapped",
               "applied, snapped" if r["status"] == "applied" and snapped and frame(moved) != frame(now) else f"{r['status']} · {frame(moved)}",
               r.get("reason"))

    # 3. duplicate a slide ---------------------------------------------------------------------------------------
    view = run.memo()
    count = lambda v, sid: sum(1 for s in v["slides"] if s["section_id"] == sid)
    several = next((s for s in view["slides"] if s["section_id"] and sections[s["section_id"]]["count"] != "one"
                    and (sections[s["section_id"]]["count_n"] is None or count(view, s["section_id"]) < sections[s["section_id"]]["count_n"])), None)
    if several is not None:
        r = await p.op({"type": "duplicate_slide", "target": several["id"]})
        v2 = run.memo()
        copy = next((s for s in v2["slides"] if s["position"] == several["position"] + 1), None)
        same_words = copy is not None and sorted(text_of(e) for e in copy["elements"]) == sorted(text_of(e) for e in several["elements"])
        ids_new = copy is not None and not ({e["id"] for e in copy["elements"]} & {e["id"] for e in several["elements"]})
        run.record("A", f"duplicate a slide of a section allowed several times ({sections[several['section_id']]['name']})",
                   "applied, a copy with the same words and its own identities",
                   "applied, a copy with the same words and its own identities" if r["status"] == "applied" and same_words and ids_new
                   else f"{r['status']} · same words {same_words} · new ids {ids_new}", r.get("reason"))
    once = next((s for s in view["slides"] if s["section_id"] and sections[s["section_id"]]["count"] == "one"), None)
    if once is not None:
        n = len(run.memo()["slides"])
        r = await p.op({"type": "duplicate_slide", "target": once["id"]})
        run.record("A", f"duplicate a slide of a section allowed once ({sections[once['section_id']]['name']})", "refused, as many slides",
                   "refused, as many slides" if r["status"] == "refused" and len(run.memo()["slides"]) == n else f"{r['status']}", r.get("reason"))

    # 4. type over a figure --------------------------------------------------------------------------------------
    view = run.memo()
    with_figure = next((e for s in view["slides"] for e in s["elements"] if e["type"] == "text" and not e.get("master")
                        and e["firm_lock"] != "fixed" for p_ in e["paragraphs"] for x in p_["segments"]
                        if x["kind"] == "chip" and any(c.isdigit() for c in x["text"]) and not x["text"].startswith("[no data")), None)
    if with_figure is not None:
        old = text_of(with_figure)
        chip = next(x["text"] for p_ in with_figure["paragraphs"] for x in p_["segments"] if x["kind"] == "chip" and any(c.isdigit() for c in x["text"]))
        r = await p.op({"type": "set_text", "target": with_figure["id"], "base_revision": with_figure["revision"],
                        "payload": {"text": old.replace(chip, "999", 1)}})
        run.record("A", "type over a figure in text", "refused, the text unchanged",
                   "refused, the text unchanged" if r["status"] == "refused" and text_of(run.element(with_figure["id"])) == old else r["status"],
                   r.get("reason"))
        r = await p.op({"type": "set_text", "target": with_figure["id"], "base_revision": with_figure["revision"],
                        "payload": {"text": "Note: " + old}})
        now = run.element(with_figure["id"])
        kept = any(x["kind"] == "chip" and x["text"] == chip for p_ in now["paragraphs"] for x in p_["segments"])
        run.record("A", "write words around a figure (control)", "applied, the figure the same",
                   "applied, the figure the same" if r["status"] == "applied" and kept and text_of(now).startswith("Note: ") else f"{r['status']} · figure kept {kept}",
                   r.get("reason"))
    _, boxes = p.get(f"/memos/{run.memo_id}/record-boxes")
    table_name = next(iter(boxes["tables"]))
    t = boxes["tables"][table_name]
    r = await p.op({"type": "add_element", "target": free["id"], "payload": {
        "slide_id": free["id"], "type": "table", "table": table_name, "rows": t["rows"][:3], "columns": t["columns"][:2],
        "x_in": zx + 3.5, "y_in": zy, "w_in": min(4.0, zw - 3.6), "h_in": 1.4}})
    table_id = (r.get("created") or [None])[0]
    run.facts = {"free": free["id"], "zone": zone, "picture": pic, "table": table_id}
    tbl = run.element(table_id) if table_id else None
    cell = next((c for c in (tbl or {}).get("table", {}).get("cells", []) if any(x["kind"] == "chip" for p_ in c["paragraphs"] for x in p_["segments"])), None)
    if cell is not None:
        r = await p.op({"type": "set_cell", "target": table_id, "base_revision": tbl["revision"],
                        "payload": {"row": cell["row"], "col": cell["col"], "text": "999"}})
        run.record("A", "type over a figure in a table cell", "refused", r["status"], r.get("reason"))

    # 5. paste a chart as a picture ---------------------------------------------------------------------------------
    status, up = p.upload(f"/memos/{run.memo_id}/pictures", "chart.png", chart_picture(), "image/png")
    r = await p.op({"type": "add_element", "target": free["id"], "payload": {
        "slide_id": free["id"], "type": "image", "asset_id": up.get("asset_id"), "alt": "chart.png",
        "x_in": zx + 3.5, "y_in": zy + 1.6, "w_in": 3.2, "h_in": 2.0}})
    pasted = (r.get("created") or [None])[0]
    pend = [x for x in run.pendings() if x["element_id"] == pasted and x["kind"] == "picture"]
    run.facts["pasted"] = pasted
    run.record("A", "paste a chart as a picture", "placed, a pending until confirmed (never taken as a chart)",
               "placed, a pending until confirmed (never taken as a chart)" if r["status"] == "applied" and pend and run.element(pasted)["type"] == "image"
               else f"{r['status']} · pending {len(pend)}", (pend[0]["message"] if pend else r.get("reason")))


def file_shapes(data: bytes) -> dict:
    """The exported file's memo shapes on each slide: id → (kind, frame, text)."""
    from app.services.roundtrip import SHAPE
    out = {}
    prs = Presentation(io.BytesIO(data))
    for slide in prs.slides:
        for sh in slide.shapes:
            m = SHAPE.match(sh.name or "")
            if not m:
                continue
            kind = "image" if sh.shape_type == 13 else "table" if getattr(sh, "has_table", False) and sh.has_table else \
                "chart" if getattr(sh, "has_chart", False) and sh.has_chart else "text"
            out[m.group(1)] = (kind, tuple(round(v / 914400, 3) for v in (sh.left, sh.top, sh.width, sh.height)))
    return out


async def part_b(run: Run, folder: Path) -> None:
    p, f = run.p, getattr(run, "facts", None)
    if not f:
        run.record("B", "the canvas slide", "built", "not built: part A stopped before it")
        return
    zx, zy, zw, zh = f["zone"]
    _, boxes = p.get(f"/memos/{run.memo_id}/record-boxes")
    family = next(iter(boxes["families"]))
    concept_view = run.memo()
    concept = next((x["concept"] for s in concept_view["slides"] for e in s["elements"] for p_ in e["paragraphs"] for x in p_["segments"]
                    if x["kind"] == "chip" and x["concept"] and not x["text"].startswith("[no data")), None)
    r1 = await p.op({"type": "add_element", "target": f["free"], "payload": {
        "slide_id": f["free"], "type": "text", "text": f"Takeaway: {{{{{concept}}}}} from the record." if concept else "Takeaway.",
        "x_in": zx, "y_in": zy + 2.1, "w_in": 3.2, "h_in": 0.8}})
    r2 = await p.op({"type": "add_element", "target": f["free"], "payload": {
        "slide_id": f["free"], "type": "chart", "series": [family], "periods": 4, "chart_type": "column",
        "x_in": zx + 7.0, "y_in": zy, "w_in": min(4.5, zw - 7.1), "h_in": 2.6}})
    text_id, chart_id = (r1.get("created") or [None])[0], (r2.get("created") or [None])[0]
    run.record("B", "build the canvas slide (text with a figure, record table, record chart, pictures)", "applied",
               "applied" if r1["status"] == r2["status"] == "applied" else f"{r1['status']}/{r2['status']}", r1.get("reason") or r2.get("reason"))
    slide_before = next(s for s in run.memo()["slides"] if s["id"] == f["free"])
    mine = {e["id"]: e for e in slide_before["elements"] if e["firm_lock"] == "free"}
    pictures_before = {eid: hashlib.sha256(p.get(f"/elements/{eid}/image")[1]).hexdigest() for eid, e in mine.items() if e["type"] == "image"}

    status, exported = p.get(f"/memos/{run.memo_id}/export")
    (folder / "export.pptx").write_bytes(exported)
    shapes = file_shapes(exported)
    status, back = p.upload(f"/memos/{run.memo_id}/upload", "export.pptx", exported,
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    run.record("B", "the export brought back untouched", "this memo's file, 0 changes",
               "this memo's file, 0 changes" if status == 200 and back.get("kind") == "bring_back" and back.get("changes") == 0
               else f"{status} {back if isinstance(back, dict) else ''}")
    slide_after = next(s for s in run.memo()["slides"] if s["id"] == f["free"])
    after = {e["id"]: e for e in slide_after["elements"]}
    per: dict = {}
    for eid, e in mine.items():
        kind = e["type"]
        checks = {}
        a = after.get(eid)
        checks["identity"] = a is not None and eid in shapes and a["revision"] == e["revision"]
        checks["kind"] = a is not None and a["type"] == kind and shapes.get(eid, ("",))[0] == kind
        checks["place"] = a is not None and all(abs(x - y) <= TOL for x, y in zip(frame(a), frame(e))) and \
            all(abs(x - y) <= TOL for x, y in zip(shapes.get(eid, ("", (0, 0, 0, 0)))[1], frame(e)))
        if kind == "text":
            checks["content"] = a is not None and text_of(a) == text_of(e) and \
                [x["kind"] for p_ in a["paragraphs"] for x in p_["segments"]] == [x["kind"] for p_ in e["paragraphs"] for x in p_["segments"]]
            checks["style"] = a is not None and [p_["style"] for p_ in a["paragraphs"]] == [p_["style"] for p_ in e["paragraphs"]]
        elif kind == "table":
            cells = lambda x: sorted((c["row"], c["col"], "".join(s["text"] for p_ in c["paragraphs"] for s in p_["segments"])) for c in x["table"]["cells"])
            checks["content"] = a is not None and cells(a) == cells(e)
            checks["style"] = a is not None and a["table"]["columns"] == e["table"]["columns"]
        elif kind == "chart":
            pts = lambda x: [(s["name"], [(q["category"], q["value"]) for q in s["points"]]) for s in x["chart"]["series"]]
            checks["content"] = a is not None and pts(a) == pts(e)
            checks["style"] = a is not None and a["chart"]["type"] == e["chart"]["type"]
        elif kind == "image":
            checks["content"] = a is not None and hashlib.sha256(p.get(f"/elements/{eid}/image")[1]).hexdigest() == pictures_before[eid]
            checks["style"] = a is not None and a.get("image") == e.get("image")
        survived = all(checks.values())
        per.setdefault(kind, [0, 0])
        per[kind][1] += 1
        per[kind][0] += survived
        if not survived:
            run.record("B", f"a {kind} back from PowerPoint", "the same element", "differs: " + ", ".join(k for k, v in checks.items() if not v))
    run.measure2 = per

    # a person edits the canvas slide in PowerPoint: moves the picture, rewrites the text
    prs = Presentation(io.BytesIO(exported))
    for slide in prs.slides:
        for sh in slide.shapes:
            if sh.name.startswith(f"memo:{f['picture']}@"):
                sh.left = sh.left + Inches(0.5)
            if text_id and sh.name.startswith(f"memo:{text_id}@") and sh.has_text_frame:
                para = sh.text_frame.paragraphs[0]
                runs = para.runs
                if runs:
                    runs[0].text = "Takeaway in PowerPoint: "
    edited = io.BytesIO()
    prs.save(edited)
    status, back = p.upload(f"/memos/{run.memo_id}/upload", "edited.pptx", edited.getvalue(),
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    pic_now, text_now = run.element(f["picture"]), run.element(text_id) if text_id else None
    moved = pic_now is not None and abs(pic_now["frame"]["x_in"] - mine[f["picture"]]["frame"]["x_in"] - 0.5) <= TOL + 0.05
    rewritten = text_now is not None and text_of(text_now).startswith("Takeaway in PowerPoint: ")
    figure_kept = text_now is not None and any(x["kind"] == "chip" for p_ in text_now["paragraphs"] for x in p_["segments"])
    run.record("B", "a person moves the picture and rewrites the text in PowerPoint", "both applied, the figure still a figure",
               "both applied, the figure still a figure" if status == 200 and moved and rewritten and figure_kept
               else f"{status} · moved {moved} · rewritten {rewritten} · figure {figure_kept} · {back if isinstance(back, dict) else ''}")


async def run_firm(server: Server, firm: str) -> Run:
    person = Person(server, f"alex@{firm}.demo")
    version, revision, _ = latest_spec_and_record(person)
    status, made = person.post("/memos", {"spec_version_id": version, "record_revision_id": revision, "stage": stage_for(firm),
                                          "memo_date": "2026-10-15", "deal_team": "Lead (lead); Associate"})
    assert status == 200, made
    run = Run(firm, person)
    run.memo_id = made["memo_id"]
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    await person.join(run.memo_id)
    try:
        await part_a(run)
        await part_b(run, folder)
    finally:
        await person.leave()
        from sqlalchemy import delete
        import uuid as _uuid
        from app.db.memo import Memo
        with SessionLocal() as s:
            s.execute(delete(Memo).where(Memo.id == _uuid.UUID(run.memo_id)))
            s.commit()
    return run


def report(runs: list[Run]) -> str:
    lines = [f"# The canvas · {datetime.now():%Y-%m-%d %H:%M}", "",
             "The brief's edits made as the canvas makes them (operations over the memo's websocket, files over the API), on a",
             "new memo per firm; each result checked in the memo as it then is. Measure 2: a canvas slide out to PowerPoint and",
             "back through the API.", "",
             "| | " + " | ".join(r.firm for r in runs) + " |", "|---|" + "---|" * len(runs)]
    ok = lambda r, part: f"{sum(1 for x in r.log if x['part'] == part and x['got'] == x['expected'])}/{sum(1 for x in r.log if x['part'] == part)}"
    lines.append("| A · the brief's edits and their controls, as the firm's rules say | " + " | ".join(ok(r, "A") for r in runs) + " |")
    lines.append("| B · canvas slide round trip checks | " + " | ".join(ok(r, "B") for r in runs) + " |")
    kinds = sorted({k for r in runs for k in r.measure2})
    for k in kinds:
        lines.append(f"| **Measure 2** · {k} back as the same element | " + " | ".join(
            f"{r.measure2[k][0]}/{r.measure2[k][1]}" if k in r.measure2 else "—" for r in runs) + " |")
    total = lambda r: (sum(v[0] for v in r.measure2.values()), sum(v[1] for v in r.measure2.values()))
    lines.append("| **Measure 2** · all elements of the canvas slide | " + " | ".join(
        f"{total(r)[0]}/{total(r)[1]} ({100 * total(r)[0] / max(1, total(r)[1]):.0f}%)" for r in runs) + " |")
    lines += ["", "## Each edit", ""]
    for r in runs:
        lines.append(f"**{r.firm}**")
        for x in r.log:
            lines.append(f"- {'✓' if x['got'] == x['expected'] else '✗'} {x['part']} · {x['what']}: {x['got']}" + (f" — *{x['detail']}*" if x["detail"] else ""))
        lines.append("")
    broke = [f"- {r.firm} · {x['part']} · {x['what']}: expected {x['expected']}, got {x['got']}" for r in runs for x in r.log if x["got"] != x["expected"]]
    lines += ["## Where it breaks", ""] + (broke or ["Nothing broke."])
    return "\n".join(lines) + "\n"


async def main_async(firms: list[str]) -> list[Run]:
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
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name)) if not args.firm or f.name == args.firm]
    runs = asyncio.run(main_async(firms))
    text = report(runs)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_canvas.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
