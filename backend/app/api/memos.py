"""Memos: list, create from a spec and a record, read for the canvas, edit through the door, the memo's room
in real time, and the PowerPoint round trip (download, upload an edited copy, what was kept aside)."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import REPO
from ..db import spec as S
from ..db.history import Conflict, Operation
from ..db.memo import Memo
from ..db.record import Deal, RecordRevision
from ..db.session import SessionLocal, get_session
from ..services import binding, roundtrip
from ..services.generator.memo import Generator
from ..services.memo_view import memo_view
from ..services.operations.door import Door
from ..services.realtime.hub import hub
from .auth import current_user

router = APIRouter(prefix="/memos", tags=["memos"])
FILES = REPO / "backend" / ".work"


def own_memo(session: Session, user: S.User, memo_id: str) -> Memo:
    try:
        memo = session.get(Memo, uuid.UUID(memo_id))
    except ValueError:
        memo = None
    if memo is None or memo.firm_id != user.firm_id:            # firms are isolated accounts
        raise HTTPException(404, "no such memo for this firm")
    return memo


@router.get("")
def list_memos(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    out = []
    for m in session.scalars(select(Memo).where(Memo.firm_id == user.firm_id).order_by(Memo.created_at.desc())):
        version = session.get(S.SpecVersion, m.spec_version_id)
        revision = session.get(RecordRevision, m.record_revision_id)
        creator = session.get(S.User, m.created_by) if m.created_by else None
        out.append({"id": str(m.id), "title": m.title, "status": m.status, "deal": session.get(Deal, m.deal_id).name,
                    "stage": session.get(S.Stage, m.stage_id).name if m.stage_id else None,
                    "spec": session.get(S.Spec, version.spec_id).name, "spec_version": version.number,
                    "record_revision": revision.number, "memo_date": m.memo_date,
                    "created_at": m.created_at.isoformat(), "created_by": creator.email if creator else None})
    return out


class NewMemo(BaseModel):
    spec_version_id: str
    record_revision_id: str
    stage: str
    memo_date: str | None = None
    deal_team: str | None = None


@router.post("")
async def create_memo(body: NewMemo, user: S.User = Depends(current_user)) -> dict:
    """A memo from a spec version, a record revision and a stage. The firm's binding is made the first time
    (one model call), if it has none for this kind of record."""
    def create():
        with SessionLocal() as session:
            firm = session.get(S.Firm, user.firm_id)
            revision = session.get(RecordRevision, uuid.UUID(body.record_revision_id))
            if revision is None or revision.firm_id != firm.id:
                raise HTTPException(404, "no such record for this firm")
            bound = session.scalar(select(S.Binding.id).where(S.Binding.firm_id == firm.id,
                                                               S.Binding.record_schema == revision.record_schema))
            firm_name = firm.name
        if bound is None:
            binding.bind(firm_name)
        with SessionLocal() as session:
            try:
                return Generator(session, firm_name, body.stage, body.memo_date, body.deal_team,
                                 spec_version_id=uuid.UUID(body.spec_version_id), revision_id=uuid.UUID(body.record_revision_id),
                                 created_by=user.id).run()
            except ValueError as e:
                raise HTTPException(400, str(e))

    return await run_in_threadpool(create)


@router.get("/{memo_id}")
def read_memo(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The memo as the canvas paints it, with the spec's styles, and who is in it right now."""
    memo = own_memo(session, user, memo_id)
    return {**memo_view(session, memo.id), "people": hub.people(str(memo.id))}


# --- edits ----------------------------------------------------------------------------------------
class OperationIn(BaseModel):
    type: str
    target: str | None = None
    base_revision: int | None = None
    payload: dict = {}


@router.post("/{memo_id}/operations")
async def apply_operation(memo_id: str, op: OperationIn, user: S.User = Depends(current_user)) -> dict:
    """Every edit goes through the door: applied, refused or kept aside, always logged; whoever has the memo
    open sees the result."""
    body = op.model_dump()
    other = hub.holder(memo_id, body.get("target"))

    def apply():
        with SessionLocal() as session:
            memo = own_memo(session, user, memo_id)
            held_by = other.email if other is not None and other.email != user.email else None
            return Door(session, memo.id).apply(body, actor_type="person", actor_id=user.id, held_by=held_by)

    result = await run_in_threadpool(apply)
    message = {"type": "operation", "by": user.email, "op": body["type"], "target": body.get("target"),
               "status": result.status, "reason": result.reason, "sequence": result.sequence,
               "element": result.element, "slides": result.slides, "created": [str(x) for x in result.created or []]}
    await hub.send(memo_id, message)
    return message


@router.websocket("/{memo_id}/live")
async def live(socket: WebSocket, memo_id: str, email: str):
    """The memo's room: presence, one person per box, live drafts, and edits through the door."""
    def allowed():
        with SessionLocal() as session:
            user = session.scalar(select(S.User).where(S.User.email == email))
            memo = session.get(Memo, uuid.UUID(memo_id))
            return user if user is not None and memo is not None and memo.firm_id == user.firm_id else None

    user = await run_in_threadpool(allowed)
    await socket.accept()
    if user is None:
        await socket.close(code=4403)                            # firms are isolated accounts
        return
    await hub.join(memo_id, socket, user.id, user.email)
    try:
        while True:
            message = await socket.receive_json()
            kind = message.get("type")
            if kind == "take":
                await hub.take(memo_id, socket, str(message.get("element_id")))
            elif kind == "release":
                await hub.release(memo_id, socket, message.get("element_id"))
            elif kind == "draft":
                await hub.draft(memo_id, socket, str(message.get("element_id")), message.get("text"))
            elif kind == "op":
                await hub.commit(memo_id, socket, message.get("op") or {})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(memo_id, socket)


# --- PowerPoint round trip ------------------------------------------------------------------------
@router.get("/{memo_id}/export")
async def export(memo_id: str, user: S.User = Depends(current_user)):
    """The memo as a PowerPoint file, painted from the database (never from the screen)."""
    with SessionLocal() as session:
        memo = own_memo(session, user, memo_id)
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in memo.title).strip() or "memo"
    out = FILES / "exports" / memo_id / f"{datetime.now():%Y%m%d_%H%M%S}.pptx"
    await run_in_threadpool(roundtrip.export, memo_id, out)
    return FileResponse(out, filename=f"{name}.pptx",
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


@router.post("/{memo_id}/import")
async def import_pptx(memo_id: str, file: UploadFile, user: S.User = Depends(current_user)) -> dict:
    """An edited copy of an exported file: each change goes through the door; what cannot be applied is kept
    aside, never lost. Everyone in the memo gets the memo again."""
    with SessionLocal() as session:
        own_memo(session, user, memo_id)
    path = FILES / "uploads" / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.pptx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(await file.read())
    try:
        result = await run_in_threadpool(roundtrip.import_pptx, memo_id, path, None, user.email)
    except Exception as e:                                       # not a file this memo exported
        raise HTTPException(400, f"could not read this file against the memo's last export: {e}")
    await hub.send(memo_id, {"type": "reload", "by": user.email, "reason": "PowerPoint import"})
    return result


@router.get("/{memo_id}/conflicts")
def conflicts(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """What was kept aside (an edit on an older version, a box added or deleted in PowerPoint…), never lost."""
    memo = own_memo(session, user, memo_id)
    out = []
    for c in session.scalars(select(Conflict).where(Conflict.memo_id == memo.id).order_by(Conflict.created_at.desc())):
        op = session.get(Operation, c.operation_id) if c.operation_id else None
        actor = session.get(S.User, op.actor_id or op.on_behalf_of) if op and (op.actor_id or op.on_behalf_of) else None
        out.append({"id": str(c.id), "kind": c.kind, "detail": c.detail, "status": c.status,
                    "element_id": str(c.element_id) if c.element_id else None, "created_at": c.created_at.isoformat(),
                    "operation": op and {"type": op.op_type, "actor": op.actor_type, "by": actor.email if actor else None,
                                         "payload": op.payload}})
    return out
