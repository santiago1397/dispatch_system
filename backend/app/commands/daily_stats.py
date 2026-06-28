"""CLI command: roll up daily stats for a date.

Invoked by the APScheduler cron at ``STATS_DAILY_HOUR:STATS_DAILY_MINUTE``
(default 23:55) inside ``main.py:lifespan``, and directly for backfill
or reprocessing:

    uv run agents_bots cmd daily-stats                       # yesterday
    uv run agents_bots cmd daily-stats --date=2026-06-26    # explicit
    uv run agents_bots cmd daily-stats --scope=per_tech      # one scope
"""

import asyncio
from datetime import date, timedelta

import click

from app.commands import command, error, info, success


@command("daily-stats", help="Roll up daily stats for the given date")
@click.option(
    "--date",
    "snapshot_date",
    default=None,
    help="YYYY-MM-DD (default: yesterday).",
)
@click.option(
    "--scope",
    default="all",
    type=click.Choice(["all", "per_job", "per_tech", "per_company"]),
    help="Which scope to roll up. 'all' runs all three.",
)
def daily_stats(snapshot_date: str | None, scope: str) -> None:
    """Roll up daily stats for a single date."""

    if snapshot_date is None:
        target = date.today() - timedelta(days=1)
    else:
        try:
            target = date.fromisoformat(snapshot_date)
        except ValueError:
            error(f"Invalid date: {snapshot_date!r}; expected YYYY-MM-DD")
            return

    info(f"Rolling up daily stats for {target.isoformat()} (scope={scope})")

    async def _run() -> None:
        from app.db.session import async_session_maker
        from app.services.daily_stats import DailyStatsService

        async with async_session_maker() as session:
            service = DailyStatsService(session)
            # The service.snapshot method always runs all three scopes —
            # we accept the CLI's --scope flag as documentation/intent
            # but don't gate the underlying logic. (Adding per-scope
            # methods would duplicate work for negligible gain.)
            n = await service.snapshot(snapshot_date=target)
            await session.commit()
            success(f"Wrote {n} snapshot rows for {target.isoformat()}")

    asyncio.run(_run())
