"""PowerPoint round trip: export a memo, and find what a person changed in an edited copy.

Identity is the shape name (memo:<element id>@<revision>) and the slide name (memo-slide:<slide id>), never
titles, order or positions. What a person changed is the difference between the file we exported and the
file that comes back: the edited file is compared with the exported one, not with the memo as it is now
(changes made in the app since the export are the door's business, through the revision).

This module only finds the differences; turning them into operations is the import's next step.

Usage (from backend/):
    python -m app.services.roundtrip export <memo id> <out.pptx>
    python -m app.services.roundtrip diff <exported.pptx> <edited.pptx>
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu


from app.db.history import Export
from app.db.memo import Asset, Memo
from app.db.session import SessionLocal
from app.services.painters.pptx import Painter

SHAPE = re.compile(r"^memo:([0-9a-f-]{36})@(\d+)$")
SLIDE = re.compile(r"^memo-slide:([0-9a-f-]{36})$")
TOL_IN = 0.02


def export(memo_id, out: Path) -> dict:
    """Paint the memo and record the export (its sequence and the file), so an edited copy can be compared."""
    with SessionLocal() as session:
        memo = session.get(Memo, uuid.UUID(str(memo_id)))
        result = Painter(session, memo.id).paint(out)
        asset = Asset(id=uuid.uuid4(), firm_id=memo.firm_id, kind="export", storage_path=str(out),
                      content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        session.add(asset)
        session.flush()
        row = Export(id=uuid.uuid4(), memo_id=memo.id, sequence=memo.sequence or 0, format="pptx", asset_id=asset.id,
                     seconds=result["seconds"])
        session.add(row)
        session.commit()
        return {"export_id": str(row.id), "sequence": row.sequence, **result}


def _inches(v) -> float:
    return round(Emu(v or 0).inches, 3)


def _text(tf) -> str:
    """A text frame's text, one line per paragraph; PowerPoint's line breaks read as new lines."""
    lines = []
    for p in tf.paragraphs:
        lines.append("".join(r.text for r in p.runs).replace("\v", "\n"))
    return re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()


def _kind(shape) -> str:
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return "picture"
    if getattr(shape, "has_table", False):
        return "table"
    if getattr(shape, "has_chart", False):
        return "chart"
    return "text" if shape.has_text_frame else "shape"


def read(path: Path) -> dict:
    """Every slide of a file by identity: its named shapes with what can be edited, and the shapes with no
    memo name (added in PowerPoint)."""
    prs = Presentation(str(path))
    slides = []
    for index, slide in enumerate(prs.slides):
        name = slide._element.cSld.get("name") or ""
        m = SLIDE.match(name)
        shapes, unnamed = {}, []
        for shape in slide.shapes:
            sm = SHAPE.match(shape.name or "")
            info = {"frame": (_inches(shape.left), _inches(shape.top), _inches(shape.width), _inches(shape.height)),
                    "type": _kind(shape)}
            if info["type"] == "table":
                info["cells"] = {(r, c): _text(shape.table.cell(r, c).text_frame)
                                 for r in range(len(shape.table.rows)) for c in range(len(shape.table.columns))}
            elif info["type"] == "chart":
                info["series"] = [(s.name, tuple(round(v, 6) if v is not None else None for v in s.values))
                                  for plot in shape.chart.plots for s in plot.series]
            elif shape.has_text_frame:
                info["text"] = _text(shape.text_frame)
            if sm:
                shapes[sm.group(1)] = {**info, "revision": int(sm.group(2))}
            else:
                unnamed.append(info)
        slides.append({"index": index, "slide_id": m.group(1) if m else None, "shapes": shapes, "unnamed": unnamed})
    return {"slides": slides}


def diff(exported: dict, edited: dict) -> list[dict]:
    """What changed from the exported file to the edited one, as a list of differences."""
    out = []
    before = {s["slide_id"]: s for s in exported["slides"] if s["slide_id"]}
    seen: dict[str, int] = {}
    order_before = [s["slide_id"] for s in exported["slides"] if s["slide_id"]]
    order_after = []
    for s in edited["slides"]:
        sid = s["slide_id"]
        if sid is None or sid not in before:
            out.append({"change": "slide_added", "index": s["index"]})
            continue
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] > 1:                                   # the same slide twice: a duplicate made in PowerPoint
            out.append({"change": "slide_duplicated", "slide_id": sid, "index": s["index"]})
            continue
        order_after.append(sid)
        b = before[sid]
        for eid, shape in s["shapes"].items():
            old = b["shapes"].get(eid)
            if old is None:
                out.append({"change": "shape_from_elsewhere", "element_id": eid, "slide_id": sid})
                continue
            base = {"element_id": eid, "revision": shape["revision"], "slide_id": sid}
            if any(abs(a - c) > TOL_IN for a, c in zip(old["frame"], shape["frame"])):
                out.append({**base, "change": "frame", "before": old["frame"], "after": shape["frame"]})
            if "text" in old and old.get("text") != shape.get("text"):
                out.append({**base, "change": "text", "before": old["text"], "after": shape.get("text")})
            if "cells" in old:
                for key, value in shape.get("cells", {}).items():
                    if old["cells"].get(key) != value:
                        out.append({**base, "change": "cell", "row": key[0], "col": key[1], "before": old["cells"].get(key), "after": value})
            if "series" in old and old["series"] != shape.get("series"):
                out.append({**base, "change": "chart_data", "before": old["series"], "after": shape.get("series")})
        for eid in set(b["shapes"]) - set(s["shapes"]):
            out.append({"change": "shape_deleted", "element_id": eid, "revision": b["shapes"][eid]["revision"], "slide_id": sid})
        for extra in s["unnamed"][len(b["unnamed"]):] if len(s["unnamed"]) > len(b["unnamed"]) else []:
            out.append({"change": "shape_added", "slide_id": sid, "type": extra["type"], "frame": extra["frame"],
                        "text": extra.get("text")})
    for sid in set(order_before) - set(order_after):
        out.append({"change": "slide_deleted", "slide_id": sid})
    kept = [sid for sid in order_before if sid in order_after]
    if kept != [sid for sid in order_after if sid in kept]:
        for position, sid in enumerate(order_after):
            if order_before.index(sid) != position:
                out.append({"change": "slide_moved", "slide_id": sid, "position": position})
    return out


def operations(changes: list[dict]) -> list[dict]:
    """Each difference as an operation for the door. The revision in the shape's name is the base: if the
    element changed in the app since the export, the door keeps the edit aside instead of overwriting."""
    ops = []
    for c in changes:
        kind, base = c["change"], {"target": c.get("element_id"), "base_revision": c.get("revision")}
        if kind == "text":
            ops.append({"type": "set_text", **base, "payload": {"text": c["after"] or ""}})
        elif kind == "frame":
            x, y, w, h = c["after"]
            ops.append({"type": "set_frame", **base, "payload": {"x_in": x, "y_in": y, "w_in": w, "h_in": h}})
        elif kind == "cell":
            ops.append({"type": "set_cell", **base, "payload": {"row": c["row"], "col": c["col"], "text": c["after"] or ""}})
        elif kind == "chart_data":
            ops.append({"type": "set_chart_data", **base, "payload": {"series": [list(s) for s in c["after"] or []]}})
        elif kind == "shape_deleted":
            ops.append({"type": "delete_element", **base})
        elif kind == "shape_added":
            x, y, w, h = c["frame"]
            ops.append({"type": "add_element", "target": c["slide_id"], "payload": {
                "slide_id": c["slide_id"], "type": "image" if c["type"] == "picture" else "chart" if c["type"] == "chart" else "text",
                "x_in": x, "y_in": y, "w_in": w, "h_in": h, "text": c.get("text")}})
        elif kind == "slide_moved":
            ops.append({"type": "move_slide", "target": c["slide_id"], "payload": {"position": c["position"]}})
        elif kind == "slide_duplicated":
            ops.append({"type": "duplicate_slide", "target": c["slide_id"]})
        elif kind == "slide_deleted":
            ops.append({"type": "delete_slide", "target": c["slide_id"]})
        elif kind == "slide_added":
            ops.append({"type": "keep_aside", "payload": {"kind": "added_slide", "reason": "a slide was added in PowerPoint"}})
        elif kind == "shape_from_elsewhere":
            ops.append({"type": "keep_aside", "target": c["element_id"], "payload": {
                "kind": "moved_box", "reason": "a box was moved to another slide in PowerPoint"}})
    return ops


def import_pptx(memo_id, edited: Path, export_id=None, actor_email: str | None = None) -> dict:
    """Bring an edited copy back: every difference from the export goes through the door as one change set."""
    from sqlalchemy import select
    from app.db import spec as S
    from app.db.history import ChangeSet, Import
    from app.services.operations.door import Door

    with SessionLocal() as session:
        memo = session.get(Memo, uuid.UUID(str(memo_id)))
        q = select(Export).where(Export.memo_id == memo.id)
        exp = session.get(Export, uuid.UUID(str(export_id))) if export_id else session.scalars(q.order_by(Export.created_at.desc())).first()
        exported = Path(session.get(Asset, exp.asset_id).storage_path)
        user = session.scalar(select(S.User).where(S.User.email == actor_email)) if actor_email else None
        changes = diff(read(exported), read(edited))
        change_set = ChangeSet(id=uuid.uuid4(), memo_id=memo.id, actor_type="import", on_behalf_of=user.id if user else None,
                               summary=f"PowerPoint import ({len(changes)} changes)", status="applied")
        session.add(change_set)
        session.commit()
        door = Door(session, memo.id)
        results = []
        for op in operations(changes):
            r = door.apply(op, actor_type="import", actor_id=user.id if user else None,
                           on_behalf_of=user.id if user else None, change_set_id=change_set.id)
            results.append({"type": op["type"], "status": r.status, "reason": r.reason})
        counts = func_count(results)
        session.add(Import(memo_id=memo.id, export_id=exp.id, change_set_id=change_set.id,
                           report="; ".join(f"{k} {v}" for k, v in counts.items()),
                           created_by=user.id if user else None))
        session.commit()
        return {"changes": len(changes), **counts, "results": results}


def func_count(results: list[dict]) -> dict:
    out: dict = {}
    for r in results:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("memo_id")
    e.add_argument("out", type=Path)
    d = sub.add_parser("diff")
    d.add_argument("exported", type=Path)
    d.add_argument("edited", type=Path)
    a = parser.parse_args()
    if a.cmd == "export":
        sys.stdout.write(f"{export(a.memo_id, a.out)}\n")
    else:
        changes = diff(read(a.exported), read(a.edited))
        sys.stdout.write(f"{len(changes)} changes\n" + "".join(f"  {c}\n" for c in changes))


if __name__ == "__main__":
    main()
