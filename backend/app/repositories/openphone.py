"""Repository for OpenPhone incoming message persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.openphone import IncomingMessage, MessageSource


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
