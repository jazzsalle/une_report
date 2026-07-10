"""FastAPI 앱 조립. 실행: uvicorn app.main:app --port 8080"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    app.state.db = database.connect()
    database.init_db(app.state.db)
    yield
    app.state.db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="hwpx-chat-editor", lifespan=lifespan)

    from app.api import (
        routes_auth,
        routes_chat,
        routes_documents,
        routes_health,
        routes_sessions,
    )

    app.include_router(routes_health.router, prefix="/api")
    app.include_router(routes_auth.router, prefix="/api")
    app.include_router(routes_documents.router, prefix="/api")
    app.include_router(routes_chat.router, prefix="/api")
    app.include_router(routes_sessions.router, prefix="/api")

    # 프론트 빌드 산출물 서빙 (web/dist가 있을 때만)
    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
