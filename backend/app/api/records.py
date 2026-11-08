"""Deal records: upload a file, read a revision, edit it. Every upload or edit is the deal's next revision; the
earlier ones stay as they were."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import REPO
from ..db import spec as S
from ..db.record import Deal, RecordRevision, RecordValue
from ..db.session import SessionLocal, get_session
from ..services import records
from .auth import current_user

router = APIRouter(prefix="/records", tags=["records"])
UPLOADS = REPO / "backend" / ".work" / "uploads"


def revision_json(session: Session, r: RecordRevision) -> dict:
    creator = session.get(S.User, r.created_by) if r.created_by else None
    return {"id": str(r.id), "number": r.number, "as_of": r.as_of.isoformat() if r.as_of else None,
            "schema": r.record_schema, "created_at": r.created_at.isoformat(), "created_by": creator.email if creator else None,
            "previous_id": str(r.previous_id) if r.previous_id else None}


def own_revision(session: Session, user: S.User, revision_id: str) -> RecordRevision:
    r = session.get(RecordRevision, uuid.UUID(revision_id))
    if r is None or r.firm_id != user.firm_id:
        raise HTTPException(404, "no such record for this firm")
    return r


@router.get("")
def list_records(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """The firm's deals, each with its revisions (newest first)."""
    revisions = session.scalars(select(RecordRevision).where(RecordRevision.firm_id == user.firm_id)
                                .order_by(RecordRevision.number.desc())).all()
    counts = dict(session.execute(select(RecordValue.revision_id, func.count()).where(
        RecordValue.revision_id.in_([r.id for r in revisions])).group_by(RecordValue.revision_id)).all())
    deals: dict = {}
    for r in revisions:
        deal = deals.setdefault(r.deal_id, {"id": str(r.deal_id), "name": session.get(Deal, r.deal_id).name, "revisions": []})
        deal["revisions"].append({**revision_json(session, r), "values": counts.get(r.id, 0)})
    return list(deals.values())


@router.get("/{revision_id}")
def read_revision(revision_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Every value of a revision, with what changed from the revision it was made from."""
    r = own_revision(session, user, revision_id)
    rows = session.scalars(select(RecordValue).where(RecordValue.revision_id == r.id)).all()
    before = {}
    if r.previous_id:
        before = {v.path: v for v in session.scalars(select(RecordValue).where(RecordValue.revision_id == r.previous_id))}

    def value(v: RecordValue):
        return v.number_value if v.kind == "number" else v.date_value.isoformat() if v.date_value else v.text_value

    out = []
    for v in rows:
        b = before.get(v.path)
        out.append({"path": v.path, "family": v.family, "label": v.label, "kind": v.kind, "value_kind": v.value_kind,
                    "value": value(v), "unit": v.unit, "period": v.period, "forecast": v.is_forecast, "source": v.source,
                    "changed": b is not None and value(b) != value(v), "before": value(b) if b is not None else None})
    return {**revision_json(session, r), "deal": session.get(Deal, r.deal_id).name, "values": out}


@router.post("")
async def upload(file: UploadFile, user: S.User = Depends(current_user)) -> dict:
    """A record file (the deal-record JSON): stored as the deal's next revision for this firm."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{file.filename or 'record.json'}"
    path.write_bytes(await file.read())

    def load():
        with SessionLocal() as session:
            firm = session.get(S.Firm, user.firm_id)
            try:
                r = records.load(session, firm.name, path, created_by=user.id)
            except (ValueError, KeyError) as e:
                raise HTTPException(400, f"could not read this record: {e}")
            return revision_json(session, r)

    return await run_in_threadpool(load)


class Changes(BaseModel):
    changes: dict[str, object]


@router.post("/{revision_id}/revise")
def revise(revision_id: str, body: Changes, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Edited values: a new revision with them; the one edited stays."""
    r = own_revision(session, user, revision_id)
    if not body.changes:
        raise HTTPException(400, "nothing changed")
    try:
        new = records.revise(session, r.id, body.changes, created_by=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return revision_json(session, new)
