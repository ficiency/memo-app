"""FastAPI app: registers the routers. Routes only receive and answer; logic lives in app/services."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import firms, health
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
