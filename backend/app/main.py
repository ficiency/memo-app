"""FastAPI app: registers the routers. Routes only receive and answer; logic lives in app/services."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, bindings, firms, health, memos, onboarding, records, specs
from .config import settings

app = FastAPI(title="memo-app")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(firms.router)
app.include_router(auth.router)
app.include_router(specs.router)
app.include_router(records.router)
app.include_router(memos.router)
app.include_router(onboarding.router)
app.include_router(bindings.router)
