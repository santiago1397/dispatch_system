"""One-off: reclassify every IncomingMessage containing an s1j.co link.

Picks up the newly-seeded QUICK_KEY_PROFESSIONAL regex (group 1 anchors on
the Confirm short-link). Runs sequentially to keep AI extraction calls
predictable. Safe to re-run — JobClassificationService reuses an
existing DispatchJob row by message id.
"""

import asyncio
import logging
import sys

from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("reclassify_quickkey")


async def main() -> int:
    from app.db.models.openphone import IncomingMessage
    from app.db.session import async_session_maker
    from app.services.classification import JobClassificationService

    pattern = r"s1j\.co/j/[A-Z0-9]+"
    summary: dict[str, int] = {}
    failures: list[tuple[str, str]] = []

    async with async_session_maker() as session:
        result = await session.execute(
            select(IncomingMessage)
            .where(IncomingMessage.content.op("~")(pattern))
            .order_by(IncomingMessage.created_at.asc())
        )
        messages = list(result.scalars().all())
        log.info("Found %d messages matching s1j.co", len(messages))

        svc = JobClassificationService(session)
        for i, msg in enumerate(messages, 1):
            try:
                job = await svc.classify_message(msg)
                await session.commit()
                status = job.classification_status or "unknown"
                summary[status] = summary.get(status, 0) + 1
                log.info("[%d/%d] %s -> %s", i, len(messages), msg.id, status)
            except Exception as exc:
                await session.rollback()
                failures.append((str(msg.id), repr(exc)))
                log.exception("[%d/%d] %s FAILED", i, len(messages), msg.id)

    log.info("Summary: %s", summary)
    if failures:
        log.warning("Failures (%d):", len(failures))
        for mid, err in failures:
            log.warning("  %s: %s", mid, err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
