"""One-off: give layout and memo elements their stacking order (z_index), written before the writer stored it.

The order is the step 3 result's box order (the template's shape order, back to front). A layout element is
matched by its layout name, its name and its frame; a memo element takes its layout element's order.

Usage (from backend/):  python -m scripts.backfill_z_index
"""
from __future__ import annotations

import sys

from sqlalchemy import select, update

from app.db import spec as S
from app.db.memo import Element
from app.db.session import SessionLocal
from app.services.onboarding.common import load_step

MASTER = "Master"


def key(name, x, y, w, h) -> tuple:
    return (name or "", round(x or 0, 2), round(y or 0, 2), round(w or 0, 2), round(h or 0, 2))


def main() -> None:
    with SessionLocal() as s:
        for firm in s.scalars(select(S.Firm).order_by(S.Firm.name)):
            step3 = load_step(firm.name, 3)
            order: dict[str, dict] = {MASTER: {}}
            for z, b in enumerate(step3.get("master") or []):
                f = b.get("frame") or {}
                order[MASTER][key(b.get("name"), f.get("x"), f.get("y"), f.get("w"), f.get("h"))] = z
            for lay in step3.get("layouts") or []:
                order[lay["name"]] = {}
                for z, b in enumerate(lay.get("boxes") or []):
                    f = b.get("frame") or {}
                    order[lay["name"]][key(b.get("name"), f.get("x"), f.get("y"), f.get("w"), f.get("h"))] = z
            matched = missed = 0
            versions = select(S.SpecVersion.id).join(S.Spec).where(S.Spec.firm_id == firm.id)
            for layout in s.scalars(select(S.Layout).where(S.Layout.spec_version_id.in_(versions))):
                boxes = order.get(MASTER if layout.is_master else layout.name, {})
                for el in s.scalars(select(Element).where(Element.layout_id == layout.id)):
                    z = boxes.get(key(el.name, el.x_in, el.y_in, el.w_in, el.h_in))
                    if z is None:
                        missed += 1
                    else:
                        el.z_index, matched = z, matched + 1
            s.flush()
            sys.stdout.write(f"{firm.name}: layout elements ordered {matched}, not matched {missed}\n")
        origin = Element.__table__.alias("origin")
        result = s.execute(update(Element).where(Element.origin_element_id == origin.c.id, Element.slide_id.isnot(None))
                           .values(z_index=origin.c.z_index))
        sys.stdout.write(f"memo elements given their layout element's order: {result.rowcount}\n")
        s.commit()


if __name__ == "__main__":
    main()
