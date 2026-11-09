"""FastAPI app: registers the routers. Routes only receive and answer; logic lives in app/services."""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, bindings, firms, health, memos, onboarding, records, specs
from .config import settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .services.fit import _index                        # the fonts on this machine, read once, not on a request
    threading.Thread(target=_index, daemon=True).start()
    yield


app = FastAPI(title="memo-app", lifespan=lifespan)
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
