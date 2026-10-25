from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.campaigns import CampaignRunner
from app.config import Settings
from app.db import connect, ensure_indexes
from app.github import GitHubClient
from app.mail import Mailer, SmtpMailer
from app.routes.campaigns import router as campaigns_router
from app.routes.contacts import router as contacts_router
from app.routes.jobs import router as jobs_router
from app.routes.presets import router as presets_router
from app.runner import JobRunner


def create_app(
    settings: Settings | None = None,
    db=None,
    github: GitHubClient | None = None,
    runner: JobRunner | None = None,
    mailer: Mailer | None = None,
    campaigns: CampaignRunner | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        owns_client = False
        owns_github = False
        if db is not None:
            await ensure_indexes(db)
            app.state.db = db
            app.state.mongo_client = None
        else:
            client, database = await connect(settings)
            owns_client = True
            app.state.mongo_client = client
            app.state.db = database
        if github is not None:
            app.state.github = github
        else:
            app.state.github = GitHubClient(
                settings.github_token,
                base_url=settings.github_api_base,
            )
            owns_github = True
        app.state.runner = runner or JobRunner(app.state.db, app.state.github)
        await app.state.runner.resume_running()
        app.state.mailer = mailer or SmtpMailer(settings)
        app.state.campaigns = campaigns or CampaignRunner(app.state.db, app.state.mailer)
        await app.state.campaigns.resume_running()
        await app.state.campaigns.copy_sent()
        await app.state.campaigns.recover_connection_failures()
        yield
        if owns_github:
            await app.state.github.aclose()
        if owns_client and app.state.mongo_client is not None:
            app.state.mongo_client.close()

    app = FastAPI(title="FetchIQ", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(jobs_router)
    app.include_router(contacts_router)
    app.include_router(presets_router)
    app.include_router(campaigns_router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
