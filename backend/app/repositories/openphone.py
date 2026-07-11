"""Repository for OpenPhone incoming message persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.openphone import IncomingMessage, MessageSource

# The counterparty (the "other side" of a conversation) has no dedicated
# column — it's derived: the sender for inbound messages, the first
# recipient for outbound ones. Shared by list_threads/count_threads so the
# grouping logic can't drift between the two queries.
_COUNTERPARTY_EXPR = case(
    (IncomingMessage.direction == "incoming", IncomingMessage.from_number),
    else_=IncomingMessage.to_numbers[0].astext,
)


async def create_incoming_message(
    db: AsyncSession,
    *,
    openphone_id: str | None,
    direction: str | None,
    from_number: str | None,
    to_numbers: list[str],
    content: str | None = None,
    status: str | None = None,
    event_type: str | None,
    phone_number_id: str | None = None,
    raw_payload: dict,
    source: str = MessageSource.OPENPHONE.value,
) -> IncomingMessage:
    """Create a new incoming message record.

    ``source`` discriminates OpenPhone (default) from WhatsApp. WhatsApp
    rows leave OpenPhone-specific fields (``openphone_id``,
    ``phone_number_id``, ``event_type``, etc.) empty and ``from_number``
    unset — the company phone-number lookup returns ``None`` and the
    classifier falls through to regex/AI.
    """
    message = IncomingMessage(
        source=source,
        openphone_id=openphone_id,
        direction=direction,
        from_number=from_number,
        to_numbers=to_numbers,
        content=content,
        status=status,
        event_type=event_type,
        phone_number_id=phone_number_id,
        raw_payload=raw_payload,
    )
    db.add(message)
    await db.flush()
    await db.refresh(message)
    return message


async def get_incoming_message(
    db: AsyncSession,
    message_id: UUID,
) -> IncomingMessage | None:
    """Get an incoming message by our internal UUID."""
    return await db.get(IncomingMessage, message_id)


async def get_by_openphone_id(
    db: AsyncSession,
    openphone_id: str,
) -> IncomingMessage | None:
    """Get an incoming message by Quo's message ID (for deduplication)."""
    query = select(IncomingMessage).where(IncomingMessage.openphone_id == openphone_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def list_incoming_messages(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[IncomingMessage]:
    """List incoming messages with pagination."""
    query = (
        select(IncomingMessage)
        .order_by(IncomingMessage.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_incoming_messages(db: AsyncSession) -> int:
    """Count total incoming messages."""
    query = select(func.count()).select_from(IncomingMessage)
    result = await db.execute(query)
    return result.scalar_one()


async def count_inbound_messages_from(
    db: AsyncSession,
    *,
    from_number: str,
    after: datetime,
    until: datetime,
) -> int:
    """Count inbound OpenPhone messages from ``from_number`` in ``(after, until]``.

    Enforces the tech-side "next two messages" window for accept/reject on
    the Quo channel (no quote affordance exists). ``after`` is the dispatch
    event time (exclusive), ``until`` the reply's ``created_at`` (inclusive).
    """
    query = (
        select(func.count())
        .select_from(IncomingMessage)
        .where(
            IncomingMessage.source == MessageSource.OPENPHONE.value,
            IncomingMessage.direction == "incoming",
            IncomingMessage.from_number == from_number,
            IncomingMessage.created_at > after,
            IncomingMessage.created_at <= until,
        )
    )
    result = await db.execute(query)
    return result.scalar_one()


async def list_threads(
    db: AsyncSession,
    *,
    phone_number_id: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Row]:
    """List OpenPhone conversation threads, most recently active first.

    One row per counterparty (see ``_COUNTERPARTY_EXPR``), carrying the
    latest message's content/direction/timestamp and a total message count
    for that counterparty. Uses a ``row_number()`` window (rather than
    ``DISTINCT ON``) so the "latest per counterparty" pick and the final
    most-recent-first ordering can differ without a second query.
    """
    filters = [IncomingMessage.source == MessageSource.OPENPHONE.value]
    if phone_number_id:
        filters.append(IncomingMessage.phone_number_id == phone_number_id)

    ranked = (
        select(
            _COUNTERPARTY_EXPR.label("counterparty"),
            IncomingMessage.content.label("last_content"),
            IncomingMessage.direction.label("last_direction"),
            IncomingMessage.created_at.label("last_created_at"),
            func.row_number()
            .over(partition_by=_COUNTERPARTY_EXPR, order_by=IncomingMessage.created_at.desc())
            .label("rn"),
            func.count()
            .over(partition_by=_COUNTERPARTY_EXPR)
            .label("message_count"),
        )
        .where(*filters, _COUNTERPARTY_EXPR.isnot(None))
        .subquery()
    )

    query = (
        select(ranked)
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.last_created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.all())


async def count_threads(
    db: AsyncSession,
    *,
    phone_number_id: str | None = None,
) -> int:
    """Count distinct OpenPhone conversation threads (counterparties)."""
    filters = [IncomingMessage.source == MessageSource.OPENPHONE.value]
    if phone_number_id:
        filters.append(IncomingMessage.phone_number_id == phone_number_id)

    query = select(func.count(func.distinct(_COUNTERPARTY_EXPR))).where(
        *filters, _COUNTERPARTY_EXPR.isnot(None)
    )
    result = await db.execute(query)
    return result.scalar_one()


def _thread_message_filters(
    *,
    counterparty: str,
    phone_number_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list:
    filters = [
        IncomingMessage.source == MessageSource.OPENPHONE.value,
        or_(
            IncomingMessage.from_number == counterparty,
            IncomingMessage.to_numbers.contains([counterparty]),
        ),
    ]
    if phone_number_id:
        filters.append(IncomingMessage.phone_number_id == phone_number_id)
    if since is not None:
        filters.append(IncomingMessage.created_at >= since)
    if until is not None:
        filters.append(IncomingMessage.created_at <= until)
    return filters


async def list_thread_messages(
    db: AsyncSession,
    *,
    counterparty: str,
    phone_number_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[IncomingMessage]:
    """List OpenPhone messages exchanged with ``counterparty``, newest-first."""
    filters = _thread_message_filters(
        counterparty=counterparty,
        phone_number_id=phone_number_id,
        since=since,
        until=until,
    )
    query = (
        select(IncomingMessage)
        .where(and_(*filters))
        .order_by(IncomingMessage.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_thread_messages(
    db: AsyncSession,
    *,
    counterparty: str,
    phone_number_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """Count OpenPhone messages exchanged with ``counterparty``."""
    filters = _thread_message_filters(
        counterparty=counterparty,
        phone_number_id=phone_number_id,
        since=since,
        until=until,
    )
    query = select(func.count()).select_from(IncomingMessage).where(and_(*filters))
    result = await db.execute(query)
    return result.scalar_one()


async def count_outbound_messages_to(
    db: AsyncSession,
    *,
    counterparty: str,
    after: datetime,
    until: datetime,
) -> int:
    """Count operator outbound OpenPhone messages to ``counterparty``.

    The OpenPhone equivalent of the WhatsApp "next two operator messages"
    window: how many outbound (operator→company) messages went to this
    counterparty in ``(after, until]``. ``after`` is the job's
    ``first_message_at`` (exclusive), ``until`` the reply's ``created_at``
    (inclusive). ``to_numbers`` is a JSONB array, matched by containment.
    """
    query = (
        select(func.count())
        .select_from(IncomingMessage)
        .where(
            IncomingMessage.source == MessageSource.OPENPHONE.value,
            IncomingMessage.direction == "outgoing",
            IncomingMessage.to_numbers.contains([counterparty]),
            IncomingMessage.created_at > after,
            IncomingMessage.created_at <= until,
        )
    )
    result = await db.execute(query)
    return result.scalar_one()
