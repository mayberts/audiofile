from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import artists, downloads, plex, search, settings, track_gaps, wanted
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="audiofile", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(downloads.router)
app.include_router(wanted.router)
app.include_router(plex.router)
app.include_router(settings.router)
app.include_router(artists.router)
app.include_router(track_gaps.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
