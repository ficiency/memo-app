"""The firm's binding (concept → where its value comes from in the deal record): review, change, confirm."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.session import get_session
from ..services import binding, binding_review
from ..services.realtime.hub import hub
from .auth import current_user

router = APIRouter(prefix="/bindings", tags=["bindings"])


@router.get("")
def review(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    firm = session.get(S.Firm, user.firm_id)
    out = binding_review.review(session, firm)
    run = binding.RUNNING.get(firm.name)
    out["binding_run"] = run and {"running": run["finished"] is None, "error": run["error"], "result": run["result"]}
    users = {u.id: u.email for u in session.scalars(select(S.User).where(S.User.firm_id == firm.id))}
    out["history"] = [{"concept": c.concept_name, "before": c.before_path or c.before_formula or c.before_kind,
                       "after": c.after_path or c.after_formula or c.after_kind, "by": users.get(c.changed_by),
                       "at": c.created_at.isoformat()}
                      for c in session.scalars(select(S.BindingChange).where(S.BindingChange.firm_id == firm.id)
                                               .order_by(S.BindingChange.created_at.desc()).limit(30))]
    return out


class Change(BaseModel):
    kind: str                     # path, derived, missing
    path: str | None = None
    formula: str | None = None


@router.post("/concepts/{concept}")
def change(concept: str, body: Change, tasks: BackgroundTasks, user: S.User = Depends(current_user),
           session: Session = Depends(get_session)) -> dict:
    """Where a concept's value comes from, set by a person (kept as a change; memos built before are offered it,
    the open ones at once)."""
    try:
        out = binding_review.change(session, session.get(S.Firm, user.firm_id), user, concept, body.kind, body.path, body.formula)
    except ValueError as e:
        raise HTTPException(400, str(e))
    tasks.add_task(hub.send_firm, user.firm_id, {"type": "record", "by": user.email, "reason": "binding"})
    return out


class Approve(BaseModel):
    concepts: list[str]


@router.post("/approve")
def approve(body: Approve, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    return {"approved": binding_review.approve(session, session.get(S.Firm, user.firm_id), user, body.concepts)}
