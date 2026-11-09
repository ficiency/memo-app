"""Firm setup: materials, the six onboarding steps (run, review, approve), and the spec version written from the
approved steps."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.session import get_session
from ..services import binding
from ..services.onboarding import service
from ..services.onboarding.writer import write
from .auth import current_user

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def versions(session: Session, firm_id) -> list[dict]:
    out = []
    for spec in session.scalars(select(S.Spec).where(S.Spec.firm_id == firm_id)):
        for v in session.scalars(select(S.SpecVersion).where(S.SpecVersion.spec_id == spec.id).order_by(S.SpecVersion.number.desc())):
            out.append({"id": str(v.id), "spec": spec.name, "number": v.number, "status": v.status,
                        "created_from": v.created_from, "created_at": v.created_at.isoformat()})
    return out


@router.get("")
def overview(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    firm = session.get(S.Firm, user.firm_id)
    return {"firm": firm.name, "materials": service.materials(firm.name), "steps": service.steps(session, firm),
            "versions": versions(session, firm.id)}


@router.get("/steps/{n}")
def step(n: int, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    firm = session.get(S.Firm, user.firm_id)
    data = service.load(firm.name, n)
    if data is None:
        raise HTTPException(404, "this step has not run yet")
    review = service.result_file(firm.name, n).with_suffix(".review.txt")
    return {"number": n, "name": service.STEPS.get(n), "result": {k: v for k, v in data.items() if k != "attempts"},
            "review": review.read_text(encoding="utf-8") if review.exists() else None}


@router.post("/steps/{n}/run")
def run_step(n: int, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    if n not in service.STEPS:
        raise HTTPException(404, "no such step")
    try:
        service.start(session.get(S.Firm, user.firm_id).name, n)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"started": n}


@router.post("/steps/{n}/approve")
def approve(n: int, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    try:
        service.approve(session, session.get(S.Firm, user.firm_id), n, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"approved": n}


@router.post("/materials")
async def upload(kind: str = Form(...), file: UploadFile = ..., user: S.User = Depends(current_user),
                 session: Session = Depends(get_session)) -> dict:
    firm = session.get(S.Firm, user.firm_id)
    try:
        return service.add_material(firm.name, kind, file.filename or "material", await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/write")
async def write_spec(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The approved steps become the spec's next version; memos already made stay on the version they used."""
    firm = session.get(S.Firm, user.firm_id)
    missing = [s["name"] for s in service.steps(session, firm) if s["number"] > 0 and s["status"] != "approved"]
    if missing:
        raise HTTPException(409, f"approve these steps first: {', '.join(missing)}")
    result = await run_in_threadpool(write, firm.name)
    binding.start(firm.name)                     # new concepts are bound now, for review before the first memo
    return {"spec": result["spec"], "version": result["version"], "rows": result["rows"], "binding": "started"}
