"""Proposals: edits made for a person to confirm (the agent's, first), never applied on their own.

A proposal is a change set with its operations. When it is made, each operation is checked by the door
without being applied: the person sees what would change (each box before and after) and what would be refused
and why. Each element operation carries the revision it was made on, so when the person applies the proposal
(through the door, one operation after the other) a box someone changed meanwhile is kept aside, never
overwritten. Discarding it leaves the memo as it is. Nothing here knows any firm.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.history import ChangeSet
from app.db.memo import Element
from app.db.session import SessionLocal
from app.services.operations.door import ELEMENT_OPS, Door


def _check_all(memo_id, ops: list[dict], actor_type: str) -> list[dict]:
    out = []
    for op in ops:
        op = dict(op)
        with SessionLocal() as session:
            target = op.get("target")
            if op.get("type") in ELEMENT_OPS and op.get("base_revision") is None and target:
                el = session.get(Element, uuid.UUID(str(target))) if _is_uuid(target) else None
                if el is not None:
                    op["base_revision"] = el.revision          # made on what the proposal saw
            c = Door(session, memo_id).check(op, actor_type=actor_type)
        out.append({"op": op, "check": {"status": c.status, "reason": c.reason, "warnings": c.warnings,
                                        "before": c.before, "after": c.after, "slides": c.slides}})
    return out


def propose(session: Session, memo_id, ops: list[dict], summary: str | None, actor_type: str = "agent",
            on_behalf_of=None, message_id=None) -> dict:
    checked = _check_all(memo_id, ops, actor_type)
    cs = ChangeSet(id=uuid.uuid4(), memo_id=uuid.UUID(str(memo_id)), actor_type=actor_type, on_behalf_of=on_behalf_of,
                   summary=summary, status="proposed", source_message_id=message_id, operations=checked)
    session.add(cs)
    session.commit()
    return as_json(cs)


def apply(session: Session, change_set_id, user_id, only: list[int] | None = None) -> tuple[dict, list, list]:
    """The operations the person chose (all, when only is None) through the door, in order, on their behalf; the
    others are kept in the proposal as not chosen. Returns the proposal, the applied items and the door's results
    (for everyone's screens)."""
    # taken once: a second confirmation arriving meanwhile (a double click) finds it already being applied
    claimed = session.execute(update(ChangeSet).where(ChangeSet.id == uuid.UUID(str(change_set_id)), ChangeSet.status == "proposed")
                              .values(status="applying").returning(ChangeSet.id)).first()
    session.commit()
    if claimed is None:
        raise ValueError("this proposal was already applied or discarded")
    cs = session.get(ChangeSet, uuid.UUID(str(change_set_id)))
    chosen = set(range(len(cs.operations))) if only is None else set(only)
    results, done, applied = [], [], []
    moved_on: dict = {}               # element → the revision this proposal's own earlier change left it at
    for i, item in enumerate(cs.operations):
        if i not in chosen:
            done.append({**item, "result": {"status": "not_chosen", "reason": None, "warnings": []}})
            continue
        op = dict(item["op"])
        target = str(op.get("target") or "")
        if target in moved_on and op.get("base_revision") is not None:
            op["base_revision"] = moved_on[target]   # two changes to one box in a proposal: the second builds on the first
        with SessionLocal() as s:
            r = Door(s, cs.memo_id).apply(op, actor_type=cs.actor_type, on_behalf_of=user_id, change_set_id=cs.id)
        if r.status == "applied" and r.element:
            moved_on[target] = r.element["revision"]
        results.append(r)
        applied.append(item)
        done.append({**item, "result": {"status": r.status, "reason": r.reason, "warnings": r.warnings or []}})
    cs.operations, cs.status, cs.decided_by = done, "applied" if applied else "rejected", user_id
    session.commit()
    return as_json(cs), applied, results


def discard(session: Session, change_set_id, user_id) -> dict:
    claimed = session.execute(update(ChangeSet).where(ChangeSet.id == uuid.UUID(str(change_set_id)), ChangeSet.status == "proposed")
                              .values(status="rejected", decided_by=user_id).returning(ChangeSet.id)).first()
    if claimed is None:
        raise ValueError("this proposal was already applied or discarded")
    cs = session.get(ChangeSet, uuid.UUID(str(change_set_id)))
    session.commit()
    return as_json(cs)


def open_for(session: Session, memo_id) -> list[dict]:
    return [as_json(cs) for cs in session.scalars(select(ChangeSet).where(
        ChangeSet.memo_id == uuid.UUID(str(memo_id)), ChangeSet.status == "proposed").order_by(ChangeSet.created_at))]


def as_json(cs: ChangeSet) -> dict:
    return {"id": str(cs.id), "summary": cs.summary, "status": cs.status, "actor": cs.actor_type,
            "created_at": cs.created_at.isoformat() if cs.created_at else datetime.now(timezone.utc).isoformat(),
            "operations": cs.operations}


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
