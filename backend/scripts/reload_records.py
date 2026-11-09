"""One-off: load each firm's deal record again with unique value names (record reader row_keys), carrying over
the values people edited since the last upload.

A revision made by the earlier reader can hold two values under one name (two rows of a table with the same
first value); which cell was in which row is not stored, so it cannot be fixed in place. The file is read
again as the deal's next revision; values people changed since the last upload (on names that were unique)
are written into it, so no edit is lost.

Usage (from backend/):  python -m scripts.reload_records <record file>
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.config import REPO
from app.db import spec as S
from app.db.record import RecordRevision, RecordValue
from app.db.session import SessionLocal
from app.services import records

VALUE = ("number_value", "text_value", "date_value")


def main() -> None:
    file = Path(sys.argv[1])
    file = file if file.is_absolute() else REPO / file
    with SessionLocal() as s:
        for firm in s.scalars(select(S.Firm).order_by(S.Firm.name)):
            revisions = s.scalars(select(RecordRevision).where(RecordRevision.firm_id == firm.id)
                                  .order_by(RecordRevision.number.desc())).all()
            if not revisions:
                continue
            latest = revisions[0]
            upload = next((r for r in revisions if r.created_by is None), revisions[-1])   # the last file loaded
            values = lambda rev: s.scalars(select(RecordValue).where(RecordValue.revision_id == rev.id)).all()
            now, then = values(latest), values(upload)
            unique = lambda rows: {r.path: r for r in rows if Counter(x.path for x in rows)[r.path] == 1}
            now_u, then_u = unique(now), unique(then)
            edited = {p: r for p, r in now_u.items() if p in then_u and
                      tuple(getattr(r, k) for k in VALUE) != tuple(getattr(then_u[p], k) for k in VALUE)}
            new = records.load(s, firm.name, file)
            if edited:
                for row in s.scalars(select(RecordValue).where(RecordValue.revision_id == new.id, RecordValue.path.in_(list(edited)))):
                    for k in VALUE:
                        setattr(row, k, getattr(edited[row.path], k))
                s.commit()
            sys.stdout.write(f"{firm.name}: revision {new.number} loaded; carried over from revision {latest.number}: "
                             f"{', '.join(f'{p} = {edited[p].number_value if edited[p].number_value is not None else edited[p].text_value}' for p in edited) or 'nothing'}\n")


if __name__ == "__main__":
    main()
