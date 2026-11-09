"""Mock auth: a person is picked by email (no password). Each firm is an isolated account, so every route
answers only with the person's own firm's specs, records and memos.

The email comes in the X-User-Email header, or as ?email= where a browser cannot send headers (images,
downloads, websockets)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.memo import Asset, Element, ElementImage, Memo, Slide
from ..db.session import get_session
from ..services.painters.pptx import materials, pick_logo

router = APIRouter(tags=["auth"])


def current_user(request: Request, session: Session = Depends(get_session)) -> S.User:
    email = request.headers.get("x-user-email") or request.query_params.get("email")
    user = session.scalar(select(S.User).where(S.User.email == (email or "").strip().lower())) if email else None
    if user is None:
        raise HTTPException(401, "pick a user")
    return user


@router.get("/me")
def me(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    firm = session.get(S.Firm, user.firm_id)
    return {"id": str(user.id), "email": user.email, "name": user.display_name or user.email.split("@")[0].title(),
            "firm": {"id": str(firm.id), "name": firm.name}}


@router.get("/users")
def users(session: Session = Depends(get_session)) -> list[dict]:
    """The mock users, for the sign-in screen."""
    firms = {f.id: f.name for f in session.scalars(select(S.Firm))}
    return [{"email": u.email, "name": u.display_name or u.email.split("@")[0].title(), "firm": firms.get(u.firm_id)}
            for u in session.scalars(select(S.User).order_by(S.User.email))]


@router.get("/elements/{element_id}/image")
def image(element_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)):
    """The picture of an image element of a memo or of a layout: the firm's logo for logo boxes (as the PowerPoint
    painter places it). Only the element's own firm can read it."""
    el = session.get(Element, uuid.UUID(element_id))
    firm_id = None
    if el is not None and el.slide_id:
        firm_id = session.get(Memo, session.get(Slide, el.slide_id).memo_id).firm_id
    elif el is not None and el.layout_id:
        version = session.get(S.SpecVersion, session.get(S.Layout, el.layout_id).spec_version_id)
        firm_id = session.get(S.Spec, version.spec_id).firm_id
    if firm_id != user.firm_id:
        raise HTTPException(404, "no such element")
    picture = session.scalar(select(ElementImage).where(ElementImage.element_id == el.id))
    asset = session.get(Asset, picture.asset_id) if picture is not None and picture.asset_id else None
    if asset is not None:                                   # a picture people added
        return FileResponse(asset.storage_path, media_type=asset.content_type)
    logo = pick_logo(materials(session.get(S.Firm, firm_id).name).get("logo") or [], el)
    if logo is None or not logo.exists():
        raise HTTPException(404, "no picture for this element")
    return FileResponse(logo)
