from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.filesystem import router as fs_router
from app.routes.peers import router as peers_router
from app.routes.transfers import router as transfers_router

from app.config import settings


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

    app.include_router(fs_router)
    app.include_router(peers_router)
    app.include_router(transfers_router)

    return app


app = create_app()
