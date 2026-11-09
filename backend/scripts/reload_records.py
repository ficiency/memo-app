"""One-off: load each firm's deal record again with unique value names (record reader row_keys), then the
values people edited since the last upload, as their own revision.

A revision made by the earlier reader can hold two values under one name (two rows of a table with the same
first value); which cell was in which row is not stored, so it cannot be fixed in place. The file is read
again as the deal's next revision (an upload, as the file says); values people changed since the last upload
(on names that were unique) follow as the next revision, made by whoever made the latest one, so no edit is
lost and an upload stays what the file says.

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


def edited_since_upload(session, latest: RecordRevision, upload: RecordRevision) -> dict:
    values = lambda rev: session.scalars(select(RecordValue).where(RecordValue.revision_id == rev.id)).all()
    unique = lambda rows: {r.path: r for r in rows if Counter(x.path for x in rows)[r.path] == 1}
    now, then = unique(values(latest)), unique(values(upload))
    return {p: r for p, r in now.items() if p in then and
            tuple(getattr(r, k) for k in VALUE) != tuple(getattr(then[p], k) for k in VALUE)}


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
            edited = edited_since_upload(s, latest, upload)
            new = records.load(s, firm.name, file)
            line = f"{firm.name}: revision {new.number} loaded from the file"
            if edited:
                kind = lambda r: r.number_value if r.kind == "number" else (r.date_value.isoformat() if r.date_value else r.text_value)
                again = records.revise(s, new.id, {p: kind(r) for p, r in edited.items()}, created_by=latest.created_by)
                line += f"; revision {again.number} carries what people edited: {', '.join(edited)}"
            sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()
