from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.db import connect, ensure_indexes


def create_app(settings: Settings | None = None, db=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        if db is not None:
            await ensure_indexes(db)
            app.state.db = db
            app.state.mongo_client = None
            yield
            return
        client, database = await connect(settings)
        app.state.mongo_client = client
        app.state.db = database
        yield
        client.close()

    app = FastAPI(title="FetchIQ", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
