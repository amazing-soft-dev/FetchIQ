import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import require_token
from app.campaigns import CampaignRunner
from app.config import Settings
from app.database import Database
from app.db import connect, ensure_indexes
from app.github import GitHubClient
from app.github_auth import GitHubAppAuth
from app.logs import configure_logging
from app.mail import Mailer, SmtpMailer
from app.migrations import run_migrations
from app.readiness import is_ready, readiness_checks
from app.routes.campaigns import router as campaigns_router
from app.routes.contacts import router as contacts_router
from app.routes.jobs import router as jobs_router
from app.routes.presets import router as presets_router
from app.runner import JobRunner
from app.schemas import ReadyOut

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    github: GitHubClient | None = None,
    runner: JobRunner | None = None,
    mailer: Mailer | None = None,
    campaigns: CampaignRunner | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        log.info(
            "api starting",
            extra={
                "database": settings.database_name,
                "github_api_base": settings.github_api_base,
                "mail_configured": bool(settings.smtp_username and settings.smtp_from),
            },
        )
        owns_client = False
        owns_github = False
        app_github = github
        if app_github is None:
            # Check the GitHub App settings before opening any connection.
            auth = GitHubAppAuth(
                app_id=settings.github_app_id,
                installation_id=settings.github_app_installation_id,
                private_key_path=settings.github_app_private_key_path,
                base_url=settings.github_api_base,
            )
            app_github = GitHubClient(auth, base_url=settings.github_api_base)
            owns_github = True
        if db is not None:
            app.state.db = db
            app.state.mongo_client = None
        else:
            client, database = await connect(settings)
            owns_client = True
            app.state.mongo_client = client
            app.state.db = database
        await run_migrations(app.state.db)
        await ensure_indexes(app.state.db)
        app.state.github = app_github
        app.state.runner = runner or JobRunner(app.state.db, app.state.github)
        await app.state.runner.resume_running()
        app.state.runner.start_sweeper()
        app.state.mailer = mailer or SmtpMailer(settings)
        app.state.campaigns = campaigns or CampaignRunner(app.state.db, app.state.mailer)
        await app.state.campaigns.resume_running()
        await app.state.campaigns.copy_sent()
        await app.state.campaigns.recover_connection_failures()
        app.state.campaigns.start_sweeper()
        yield
        log.info("api stopping")
        await app.state.runner.stop_sweeper()
        await app.state.campaigns.stop_sweeper()
        if owns_github:
            await app.state.github.aclose()
        if owns_client and app.state.mongo_client is not None:
            await app.state.mongo_client.close()

    app = FastAPI(title="FetchIQ", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    guard = [Depends(require_token)]
    app.include_router(jobs_router, dependencies=guard)
    app.include_router(contacts_router, dependencies=guard)
    app.include_router(presets_router, dependencies=guard)
    app.include_router(campaigns_router, dependencies=guard)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness: constant while the process runs."""

        return {"status": "ok"}

    @app.get("/api/ready", response_model=ReadyOut, responses={503: {"model": ReadyOut}})
    async def ready(request: Request) -> JSONResponse:
        """Readiness: MongoDB answers and the GitHub App token can be minted."""

        checks = await readiness_checks(request.app)
        ok = is_ready(checks)
        return JSONResponse(
            {"status": "ok" if ok else "failed", "checks": checks},
            status_code=200 if ok else 503,
        )

    return app


app = create_app()
