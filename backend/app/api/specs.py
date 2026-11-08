import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.session import get_session
from ..services.memo_view import layouts_view
from .auth import current_user

router = APIRouter(prefix="/specs", tags=["specs"])


@router.get("")
def list_specs(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """The firm's specs, each with its versions (newest first) and the stages a memo can be created at."""
    out = []
    for spec in session.scalars(select(S.Spec).where(S.Spec.firm_id == user.firm_id).order_by(S.Spec.name)):
        versions = []
        for v in session.scalars(select(S.SpecVersion).where(S.SpecVersion.spec_id == spec.id).order_by(S.SpecVersion.number.desc())):
            stages = session.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id).order_by(S.Stage.position)).all()
            versions.append({"id": str(v.id), "number": v.number, "label": v.label, "status": v.status,
                             "created_from": v.created_from, "created_at": v.created_at.isoformat(),
                             "page": [v.page_w_in, v.page_h_in], "stages": [s.name for s in stages]})
        out.append({"id": str(spec.id), "name": spec.name, "versions": versions})
    return out


def own_version(session: Session, user: S.User, version_id: str) -> S.SpecVersion:
    v = session.get(S.SpecVersion, uuid.UUID(version_id))
    if v is None or session.get(S.Spec, v.spec_id).firm_id != user.firm_id:
        raise HTTPException(404, "no such spec version for this firm")
    return v


@router.get("/versions/{version_id}/layouts")
def layouts(version_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The version's layouts as sample slides, painted by the canvas, for review."""
    return layouts_view(session, own_version(session, user, version_id).id)


@router.get("/versions/{version_id}/decisions")
def decisions(version_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """Where the materials disagree (with each side's evidence) and the questions the onboarding left open."""
    v = own_version(session, user, version_id)
    out = []
    for d in session.scalars(select(S.ConflictDecision).where(S.ConflictDecision.spec_version_id == v.id)):
        options = session.scalars(select(S.ConflictOption).where(S.ConflictOption.decision_id == d.id)).all()
        who = session.get(S.User, d.decided_by) if d.decided_by else None
        out.append({"id": str(d.id), "topic": d.topic, "chosen": str(d.chosen_option_id) if d.chosen_option_id else None,
                    "decided_by": who.email if who else None,
                    "options": [{"id": str(o.id), "source": o.option_source, "value": o.value, "evidence": o.evidence} for o in options]})
    return out


class Choice(BaseModel):
    option_id: str


@router.post("/versions/{version_id}/decisions/{decision_id}")
def decide(version_id: str, decision_id: str, body: Choice, user: S.User = Depends(current_user),
           session: Session = Depends(get_session)) -> dict:
    """A person chooses between the materials; the choice is stored with who made it."""
    v = own_version(session, user, version_id)
    d = session.get(S.ConflictDecision, uuid.UUID(decision_id))
    option = session.get(S.ConflictOption, uuid.UUID(body.option_id))
    if d is None or d.spec_version_id != v.id or option is None or option.decision_id != d.id:
        raise HTTPException(404, "no such decision")
    d.chosen_option_id, d.decided_by = option.id, user.id
    session.commit()
    return {"id": str(d.id), "chosen": str(option.id), "decided_by": user.email}
