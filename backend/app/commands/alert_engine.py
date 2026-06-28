"""CLI command: run one pass of the pipeline alert engine.

Invoked by the APScheduler job in ``main.py:lifespan`` every
``ALERT_ENGINE_INTERVAL_MINUTES``, and directly for ad-hoc scans /
smoke tests:

    uv run agents_bots cmd alert-engine
    uv run agents_bots cmd alert-engine --dry-run

``--dry-run`` opens a session, runs the scan, logs counts, and
rolls back. The default behaviour commits so new alert rows persist.
"""

import asyncio

import click

from app.commands import command, info, success


@command("alert-engine", help="Run one pass of the pipeline alert engine")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run the scan but roll back so no alerts are persisted. Useful for "
    "smoke-testing threshold changes against prod data.",
)
def alert_engine(dry_run: bool) -> None:
    """Run a single scan pass over the pipeline health state."""

    async def _run() -> None:
        from app.db.session import async_session_maker
        from app.services.alerts import AlertEngine

        async with async_session_maker() as session:
            counts = await AlertEngine(session).scan()
            if dry_run:
                await session.rollback()
                info("[DRY RUN] no alerts persisted")
            else:
                await session.commit()
            for kind, n in counts.created.items():
                success(f"created.{kind}: {n}")
            for kind, n in counts.already_open.items():
                info(f"already_open.{kind}: {n}")

    asyncio.run(_run())
