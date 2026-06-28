"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi_pagination import add_pagination

from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.core.logfire_setup import instrument_app, setup_logfire
from app.core.middleware import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown events.

    Resources yielded here are available via request.state in route handlers.
    See: https://asgi.readthedocs.io/en/latest/specs/lifespan.html#lifespan-state
    """
    # === Startup ===
    # Surface stdlib `logger.info(...)` calls (used by the services for
    # end-to-end pipeline tracing) on stderr. Without this, the default
    # WARNING-level filter hides the happy path. Gated on non-production
    # so a prod deploy doesn't suddenly inherit INFO-level volume from
    # third-party libs (asyncpg, uvicorn, etc.).
    if settings.ENVIRONMENT != "production":
        import logging
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        # %(process)d is the OS PID — every log line now carries the worker
        # that emitted it. With uvicorn --reload each reload respawns the
        # worker via multiprocessing.spawn, so a stale orphan PID is
        # immediately visible if you grep for it.
        log_format = "%(asctime)s %(levelname)-5s [%(name)s] [pid=%(process)d] %(message)s"
        logging.basicConfig(level=logging.INFO, format=log_format)

        # Persist non-prod logs to backend/logs/backend.log so a uvicorn
        # restart doesn't wipe the diagnostic trail. 5 MB x 3 rotations.
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "backend.log", maxBytes=5_000_000, backupCount=3
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_format))

        # Suppress SQLAlchemy echo from the file handler — the SQL stream is
        # already huge and drowns the CLASSIFY_* markers we care about.
        class _NoSQLAlchemy(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return not record.name.startswith("sqlalchemy.engine")

        file_handler.addFilter(_NoSQLAlchemy())
        logging.getLogger().addHandler(file_handler)

        # Separate errors.log captures ERROR+ with full tracebacks. Without
        # this, the unhandled_exception_handler's logger.exception(...) call
        # lands in backend.log mixed with INFO noise — easy to miss. The
        # traceback is included automatically by Formatter when exc_info is
        # set on the record (which logger.exception does).
        errors_handler = RotatingFileHandler(
            log_dir / "errors.log", maxBytes=5_000_000, backupCount=3
        )
        errors_handler.setLevel(logging.ERROR)
        errors_handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(errors_handler)

    setup_logfire()
    from app.core.logfire_setup import instrument_asyncpg

    instrument_asyncpg()

    # === Process identity + port collision check ===
    # Logs WORKER_START and PORT_COLLISION markers so a stale orphan worker
    # from a previous uvicorn session is loud, not silent. With the orphan
    # problem from 2026-06-27 this was the difference between "login is
    # broken, no idea why" (hours of debugging) and "PORT_COLLISION
    # existing_pid=3824 — kill it" (seconds).
    from app.core.observability import check_port_collision, get_process_identity

    identity = get_process_identity()
    logging.info(
        "WORKER_START service=%s env=%s pid=%d parent_pid=%d worker_id=%s",
        settings.PROJECT_NAME,
        settings.ENVIRONMENT,
        identity["pid"],
        identity["parent_pid"],
        identity["worker_id"],
    )

    collision = check_port_collision("127.0.0.1", settings.PORT, own_pid=identity["pid"])
    if collision:
        cmdline = collision.get("existing_cmdline", "<unknown>")
        age = collision.get("existing_age_seconds")
        age_str = f" age={age:.0f}s" if isinstance(age, (int, float)) else ""
        logging.warning(
            "PORT_COLLISION host=%s port=%d existing_pid=%d%s cmdline=%r — "
            "another process is already listening on this port. If you just "
            "'restarted' uvicorn, an orphaned worker from a previous session "
            "is squatting on the socket. Kill it: "
            "taskkill //F //PID %d  (or list all python procs with "
            "`tasklist | grep python.exe` and kill anything on port %d).",
            collision["host"],
            collision["port"],
            collision["existing_pid"],
            age_str,
            cmdline,
            collision["existing_pid"],
            collision["port"],
        )

    # Start persistent browser
    try:
        from app.browser.manager import browser_manager

        await browser_manager.start()
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Browser manager failed to start — browser automation disabled", exc_info=True
        )

    # === Lifecycle pipeline scheduler (alerts + daily stats) ===
    # APScheduler is started in-process. Gated on ENVIRONMENT != "test"
    # so the pytest suite doesn't fire background scans. In production,
    # single-worker deploys run scans in-band; multi-worker deploys
    # should disable this and run `agents_bots cmd scheduler-runner`
    # on a dedicated sidecar (deferred per MVP cut list).
    scheduler = None
    if settings.SCHEDULER_ENABLED and settings.ENVIRONMENT != "test":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            from app.db.session import async_session_maker
            from app.services.alerts import AlertEngine

            scheduler = AsyncIOScheduler()

            async def _run_alert_scan() -> None:
                async with async_session_maker() as session:
                    counts = await AlertEngine(session).scan()
                    await session.commit()
                    logging.info(
                        "SCHEDULER_ALERT_SCAN_DONE total_created=%d",
                        counts.total_created(),
                    )

            scheduler.add_job(
                _run_alert_scan,
                "interval",
                minutes=settings.ALERT_ENGINE_INTERVAL_MINUTES,
                id="alert_engine",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

            # Phase-6 daily stats cron.
            from app.services.daily_stats import DailyStatsService

            async def _run_daily_stats() -> None:
                from datetime import date, timedelta

                yesterday = date.today() - timedelta(days=1)
                async with async_session_maker() as session:
                    n = await DailyStatsService(session).snapshot(snapshot_date=yesterday)
                    await session.commit()
                    logging.info(
                        "SCHEDULER_DAILY_STATS_DONE date=%s snapshots=%d",
                        yesterday.isoformat(),
                        n,
                    )

            scheduler.add_job(
                _run_daily_stats,
                "cron",
                hour=settings.STATS_DAILY_HOUR,
                minute=settings.STATS_DAILY_MINUTE,
                id="daily_stats",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

            scheduler.start()
            logging.info(
                "SCHEDULER_START alert_interval=%dm stats_cron=%02d:%02d",
                settings.ALERT_ENGINE_INTERVAL_MINUTES,
                settings.STATS_DAILY_HOUR,
                settings.STATS_DAILY_MINUTE,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Scheduler failed to start — alerts + daily stats will not run"
            )

    yield

    # === Shutdown ===
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
            logging.info("SCHEDULER_STOP")
        except Exception:
            logging.getLogger(__name__).exception("Scheduler shutdown failed")

    from app.db.session import close_db

    await close_db()

    # Stop persistent browser
    try:
        from app.browser.manager import browser_manager

        await browser_manager.stop()
    except Exception:
        pass


# Environments where API docs should be visible
SHOW_DOCS_ENVIRONMENTS = ("local", "staging", "development")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Only show docs in allowed environments (hide in production)
    show_docs = settings.ENVIRONMENT in SHOW_DOCS_ENVIRONMENTS
    openapi_url = f"{settings.API_V1_STR}/openapi.json" if show_docs else None
    docs_url = "/docs" if show_docs else None
    redoc_url = "/redoc" if show_docs else None

    # OpenAPI tags for better documentation organization
    openapi_tags = [
        {
            "name": "health",
            "description": "Health check endpoints for monitoring and Kubernetes probes",
        },
        {
            "name": "auth",
            "description": "Authentication endpoints - login, register, token refresh",
        },
        {
            "name": "users",
            "description": "User management endpoints",
        },
        {
            "name": "sessions",
            "description": "Session management - view and manage active login sessions",
        },
        {
            "name": "items",
            "description": "Example CRUD endpoints demonstrating the API pattern",
        },
        {
            "name": "conversations",
            "description": "AI conversation persistence - manage chat history",
        },
        {
            "name": "agent",
            "description": "AI agent WebSocket endpoint for real-time chat",
        },
        {
            "name": "whatsapp",
            "description": "WhatsApp Web scraper ingestion — service-token auth, batch message upsert, and tracked-chat whitelist management",
        },
    ]

    app = FastAPI(
        title=settings.PROJECT_NAME,
        summary="FastAPI application with Logfire observability",
        description="""
Project to work as microservice axuliar for main application

## Features
- **Authentication**: JWT-based authentication with refresh tokens
- **Database**: Async database operations
- **AI Agent**: LangChain-powered conversational assistant
- **Observability**: Logfire integration for tracing and monitoring

## Documentation

- [Swagger UI](/docs) - Interactive API documentation
- [ReDoc](/redoc) - Alternative documentation view
        """.strip(),
        version="0.1.0",
        openapi_url=openapi_url,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_tags=openapi_tags,
        contact={
            "name": "Santiago",
            "email": "santiagovillahermosa@gmail.com",
        },
        license_info={
            "name": "MIT",
            "identifier": "MIT",
        },
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
    )
    # Logfire instrumentation
    instrument_app(app)

    # Request ID middleware (for request correlation/debugging)
    app.add_middleware(RequestIDMiddleware)

    # Exception handlers
    register_exception_handlers(app)

    # CORS middleware
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
    )

    # Prometheus metrics
    from prometheus_fastapi_instrumentator import Instrumentator

    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=[
            "/health",
            "/health/ready",
            "/health/live",
            settings.PROMETHEUS_METRICS_PATH,
        ],
        inprogress_name="http_requests_inprogress",
        inprogress_labels=True,
    )
    instrumentator.instrument(app).expose(
        app,
        endpoint=settings.PROMETHEUS_METRICS_PATH,
        include_in_schema=settings.PROMETHEUS_INCLUDE_IN_SCHEMA,
    )

    # API Version Deprecation (uncomment when deprecating old versions)
    # Example: Mark v1 as deprecated when v2 is ready
    # from app.api.versioning import VersionDeprecationMiddleware
    # app.add_middleware(
    #     VersionDeprecationMiddleware,
    #     deprecated_versions={
    #         "v1": {
    #             "sunset": "2025-12-31",
    #             "link": "/docs/migration/v2",
    #             "message": "Please migrate to API v2",
    #         }
    #     },
    # )

    # Include API router
    app.include_router(api_router, prefix=settings.API_V1_STR)

    # Pagination
    add_pagination(app)

    return app


app = create_app()
