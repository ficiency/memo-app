from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.memo import Element
from ..db.session import get_session

router = APIRouter(prefix="/firms", tags=["firms"])


@router.get("")
def list_firms(session: Session = Depends(get_session)) -> list[dict]:
    """Firms with their mock users and specs (latest version and what it holds)."""
    out = []
    for firm in session.scalars(select(S.Firm).order_by(S.Firm.name)):
        users = session.scalars(select(S.User).where(S.User.firm_id == firm.id).order_by(S.User.email)).all()
        specs = []
        for spec in session.scalars(select(S.Spec).where(S.Spec.firm_id == firm.id).order_by(S.Spec.name)):
            version = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == spec.id)
                                     .order_by(S.SpecVersion.number.desc()))
            counts = {}
            if version:
                v = version.id
                layout_ids = select(S.Layout.id).where(S.Layout.spec_version_id == v)
                counts = {
                    "sections": session.scalar(select(func.count()).where(S.Section.spec_version_id == v)),
                    "layouts": session.scalar(select(func.count()).where(S.Layout.spec_version_id == v)),
                    "elements": session.scalar(select(func.count()).where(Element.layout_id.in_(layout_ids))),
                    "rules": session.scalar(select(func.count()).where(S.Rule.spec_version_id == v)),
                    "concepts": session.scalar(select(func.count()).where(S.Concept.spec_version_id == v)),
                }
            specs.append({
                "id": str(spec.id), "name": spec.name,
                "version": version and {"id": str(version.id), "number": version.number, "label": version.label,
                                        "status": version.status, "page": [version.page_w_in, version.page_h_in]},
                "counts": counts,
            })
        out.append({"id": str(firm.id), "name": firm.name,
                    "users": [{"id": str(u.id), "email": u.email} for u in users], "specs": specs})
    return out
