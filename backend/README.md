# Backend

FastAPI + SQLAlchemy 2 + Alembic on Postgres (Supabase). Settings come from the repo's `.env`
(`DATABASE_URL`, `OPENAI_API_KEY`).

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/alembic upgrade head                 # create / update the tables
.venv/Scripts/python -m app.services.onboarding.writer <firm>   # write a firm's approved onboarding as a new spec version
.venv/Scripts/uvicorn app.main:app --reload        # http://localhost:8000/health
```

| Folder | What lives there |
|---|---|
| `app/api/` | HTTP and websocket routes: receive and answer, no logic |
| `app/services/` | the logic, one folder per responsibility: onboarding, generator, operations, painters, realtime, agent |
| `app/db/` | models, one file per store: `spec.py`, `record.py`, `memo.py`, `history.py` (docs/SCHEMA_DESIGN.md) |
| `app/readers/` | PPTX and PDF readers |
| `migrations/` | Alembic migrations. Every table has row level security on with no policies: only the backend (postgres role) reads and writes; Supabase's public REST API sees nothing. New tables need the same (`ALTER TABLE … ENABLE ROW LEVEL SECURITY`) |
| `scripts/` | one-off tasks |

No firm names in code: firms are data.
