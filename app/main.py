from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes.peers import router as peers_router
from app.routes.transfers import router as transfers_router
from app.routes.library import router as library_router
from app.routes.remote import router as remote_router

from app.config import settings

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Secure P2P Transfer")

    origins = settings.cors_origins.split(",") if settings.cors_origins else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True if origins != ["*"] else False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(peers_router)
    app.include_router(transfers_router)
    app.include_router(library_router)
    app.include_router(remote_router)

    # Serve the single-page UI at the root. Mounted last so /api/* routes win.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

    return app


app = create_app()
