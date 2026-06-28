"""Repository for WhatsApp Web scraper persistence.

Mirrors ``app/repositories/openphone.py`` — module-level async functions,
``db.flush()`` not ``commit()``, ``db.refresh()`` after flush.

The bulk path is ``batch_upsert_messages``: a single
``INSERT ... ON CONFLICT DO UPDATE`` with a timestamp guard, so an older
message can never overwrite a newer one (matters because WhatsApp reuses
``wa_message_id`` after a delete-and-resend). ``upsert_message`` is kept
for any future per-message route.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.whatsapp import WhatsappMessage, WhatsappTrackedChat

# =============================================================================
# Tracked Chats
# =============================================================================


async def get_chat_by_jid(
    db: AsyncSession,
    chat_jid: str,
) -> WhatsappTrackedChat | None:
    """Get a tracked chat by its stable WhatsApp JID."""
    query = select(WhatsappTrackedChat).where(WhatsappTrackedChat.chat_jid == chat_jid)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def list_active_chats(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
) -> list[WhatsappTrackedChat]:
    """List tracked chats. By default returns only active ones.

    The extension uses this on startup to cache the whitelist in
    ``chrome.storage.local``.
    """
    query = select(WhatsappTrackedChat).order_by(WhatsappTrackedChat.display_name)
    if not include_inactive:
        query = query.where(WhatsappTrackedChat.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def upsert_chat(
    db: AsyncSession,
    *,
    chat_jid: str,
    display_name: str,
    is_group: bool = True,
    is_active: bool = True,
) -> WhatsappTrackedChat:
    """Insert or update a tracked chat by JID.

    Idempotent — the extension can call this repeatedly with the same JID.
    ``display_name`` is overwritten with the latest value (popup may have
    edited it, or the user renamed the group in WhatsApp).
    """
    stmt = pg_insert(WhatsappTrackedChat).values(
        chat_jid=chat_jid,
        display_name=display_name,
        is_group=is_group,
        is_active=is_active,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["chat_jid"],
        set_={
            "display_name": stmt.excluded.display_name,
            "is_group": stmt.excluded.is_group,
            "is_active": stmt.excluded.is_active,
        },
    )
    await db.execute(stmt)
    return await get_chat_by_jid(db, chat_jid)  # type: ignore[return-value]


async def update_chat_last_seen(
    db: AsyncSession,
    chat: WhatsappTrackedChat,
    *,
    wa_message_id: str,
    scraped_at: datetime,
) -> None:
    """Bump the ``last_scraped_at`` and ``last_seen_message_id`` after a batch."""
    chat.last_scraped_at = scraped_at
    chat.last_seen_message_id = wa_message_id
    db.add(chat)
    await db.flush()


async def set_chat_active(
    db: AsyncSession,
    chat: WhatsappTrackedChat,
    *,
    is_active: bool,
) -> WhatsappTrackedChat:
    """Soft-disable or re-enable a chat. History is preserved either way."""
    chat.is_active = is_active
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return chat


async def update_chat_display_name(
    db: AsyncSession,
    chat: WhatsappTrackedChat,
    *,
    display_name: str,
) -> WhatsappTrackedChat:
    """Rename a tracked chat's display label."""
    chat.display_name = display_name
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return chat


async def update_chat_role(
    db: AsyncSession,
    chat: WhatsappTrackedChat,
    *,
    chat_role: str,
) -> WhatsappTrackedChat:
    """Set the routing tag on a tracked chat.

    ``chat_role='tech_dispatch'`` makes the chat a candidate for the
    operator-dispatch detector in ``whatsapp.py:ingest_batch``. Any other
    value resets it to the default ``'other'`` so the chat falls back to
    the customer-facing mirror + classify path.
    """
    chat.chat_role = chat_role
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return chat


# =============================================================================
# Messages
# =============================================================================


async def upsert_message(
    db: AsyncSession,
    *,
    wa_message_id: str,
    chat_jid: str,
    sender_jid: str | None = None,
    sender_name: str | None = None,
    is_from_me: bool = False,
    body: str | None = None,
    timestamp: datetime,
    edited_at: datetime | None = None,
    is_deleted: bool = False,
    quoted_wa_message_id: str | None = None,
    media_type: str | None = None,
    media_mime: str | None = None,
    media_filename: str | None = None,
    media_size_bytes: int | None = None,
    media_caption: str | None = None,
    media_url: str | None = None,
    reactions: list | None = None,
    is_system_message: bool = False,
    system_event_type: str | None = None,
    raw_payload: dict | None = None,
) -> tuple[WhatsappMessage, bool, bool]:
    """Insert or update a message, returning ``(row, was_inserted, was_updated)``.

    The conflict resolution updates the row when:
    - the (chat_jid, wa_message_id) pair already exists, AND
    - the new row's ``timestamp`` is >= the existing row's timestamp.

    If the new row is OLDER than the existing one, no update happens and
    ``was_updated`` is False — the existing row is preserved. This is the
    "timestamp guard" the design specifies.

    Returns ``(row, inserted, updated)``:
    - inserted: True if a new row was created
    - updated: True if an existing row was updated (timestamp guard passed)
    - both False: skipped because the new row was older than the existing one
    """
    values = {
        "wa_message_id": wa_message_id,
        "chat_jid": chat_jid,
        "sender_jid": sender_jid,
        "sender_name": sender_name,
        "is_from_me": is_from_me,
        "body": body,
        "timestamp": timestamp,
        "edited_at": edited_at,
        "is_deleted": is_deleted,
        "quoted_wa_message_id": quoted_wa_message_id,
        "media_type": media_type,
        "media_mime": media_mime,
        "media_filename": media_filename,
        "media_size_bytes": media_size_bytes,
        "media_caption": media_caption,
        "media_url": media_url,
        "reactions": reactions if reactions is not None else [],
        "is_system_message": is_system_message,
        "system_event_type": system_event_type,
        "raw_payload": raw_payload if raw_payload is not None else {},
    }

    # First, check if the row exists so we can decide insert vs update vs skip.
    existing_query = select(WhatsappMessage).where(
        and_(
            WhatsappMessage.chat_jid == chat_jid,
            WhatsappMessage.wa_message_id == wa_message_id,
        )
    )
    existing = (await db.execute(existing_query)).scalar_one_or_none()

    if existing is None:
        stmt = pg_insert(WhatsappMessage).values(**values)
        await db.execute(stmt)
        row = (
            await db.execute(
                select(WhatsappMessage).where(
                    and_(
                        WhatsappMessage.chat_jid == chat_jid,
                        WhatsappMessage.wa_message_id == wa_message_id,
                    )
                )
            )
        ).scalar_one()
        return row, True, False

    if timestamp < existing.timestamp:
        # Older message — preserve the existing row.
        return existing, False, False

    # Update in place. Set every field the extension might revise.
    for col, value in values.items():
        setattr(existing, col, value)
    db.add(existing)
    await db.flush()
    await db.refresh(existing)
    return existing, False, True


async def get_message_by_id(
    db: AsyncSession,
    message_id: UUID,
) -> WhatsappMessage | None:
    """Get a single message by internal UUID."""
    return await db.get(WhatsappMessage, message_id)


@dataclass
class BatchUpsertResult:
    """Outcome of ``batch_upsert_messages``.

    ``inserted`` and ``updated`` come from PostgreSQL's ``RETURNING``
    clause; ``xmax = 0`` distinguishes a new row from an updated one.
    ``skipped`` is derived: the message existed but lost the timestamp
    guard (existing row was newer), so the UPDATE didn't run and the
    row doesn't appear in RETURNING at all.
    ``deduplicated`` counts rows removed before SQL because the same
    ``(chat_jid, wa_message_id)`` appeared more than once in the
    incoming batch (PostgreSQL's ``ON CONFLICT DO UPDATE`` cannot
    process a single row twice in one statement).
    """

    inserted: int
    updated: int
    skipped: int
    deduplicated: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)


# Columns copied from the proposed row into the existing row on conflict.
# Excludes the dedup key (``chat_jid``, ``wa_message_id``) — that pair is
# the conflict target and can't appear in ``SET``.
_MESSAGE_UPDATE_COLUMNS = (
    "sender_jid",
    "sender_name",
    "is_from_me",
    "body",
    "timestamp",
    "edited_at",
    "is_deleted",
    "quoted_wa_message_id",
    "media_type",
    "media_mime",
    "media_filename",
    "media_size_bytes",
    "media_caption",
    "media_url",
    "reactions",
    "is_system_message",
    "system_event_type",
    "raw_payload",
)


def _message_to_row(msg) -> dict:
    """Flatten a Pydantic ``WhatsappMessageCreate`` (or anything with the
    same attrs) into a row dict ready for ``pg_insert().values([...])``.
    """
    return {
        "wa_message_id": msg.wa_message_id,
        "chat_jid": msg.chat_jid,
        "sender_jid": msg.sender_jid,
        "sender_name": msg.sender_name,
        "is_from_me": msg.is_from_me,
        "body": msg.body,
        "timestamp": msg.timestamp,
        "edited_at": msg.edited_at,
        "is_deleted": msg.is_deleted,
        "quoted_wa_message_id": msg.quoted_wa_message_id,
        "media_type": msg.media_type,
        "media_mime": msg.media_mime,
        "media_filename": msg.media_filename,
        "media_size_bytes": msg.media_size_bytes,
        "media_caption": msg.media_caption,
        "media_url": msg.media_url,
        "reactions": msg.reactions if msg.reactions is not None else [],
        "is_system_message": msg.is_system_message,
        "system_event_type": msg.system_event_type,
        "raw_payload": msg.raw_payload if msg.raw_payload is not None else {},
    }


async def batch_upsert_messages(
    db: AsyncSession,
    messages: list,
) -> BatchUpsertResult:
    """Bulk insert/update messages in a single round-trip.

    Issues one ``INSERT ... ON CONFLICT (chat_jid, wa_message_id) DO UPDATE
    SET ... WHERE EXCLUDED.timestamp >= whatsapp_messages.timestamp``. The
    WHERE clause is the timestamp guard: an older message never overwrites
    a newer one, even when the (chat_jid, wa_message_id) key matches.

    Counts:
    - ``inserted``: new rows (``xmax = 0`` in RETURNING).
    - ``updated``:  existing rows whose timestamp was bumped.
    - ``skipped``:  deduped messages that lost the timestamp guard against
      an existing row.
    - ``deduplicated``: incoming messages removed before SQL because the
      same ``(chat_jid, wa_message_id)`` appeared more than once in the
      batch. Without this dedup, PostgreSQL raises
      ``CardinalityViolationError`` because a single ``ON CONFLICT DO
      UPDATE`` statement cannot affect the same row twice.

    Validation must happen before this call — if any row violates a
    column constraint (e.g. ``wa_message_id`` longer than 100 chars),
    PostgreSQL rejects the whole statement. The service layer treats
    that as a batch failure and reports per-item.
    """
    if not messages:
        return BatchUpsertResult(inserted=0, updated=0, skipped=0)

    # Dedupe by (chat_jid, wa_message_id). The extension's SW can re-emit
    # the same message multiple times in one batch (e.g. when flushing a
    # write-through buffer on top of an in-flight batch). PostgreSQL's
    # ON CONFLICT DO UPDATE cannot process a single row twice in one
    # statement — it raises CardinalityViolationError. On duplicate, keep
    # the row with the latest timestamp; ties resolve to the first seen.
    seen: dict[tuple[str, str], int] = {}
    deduped: list[dict] = []
    for msg in messages:
        row = _message_to_row(msg)
        key = (row["chat_jid"], row["wa_message_id"])
        existing_idx = seen.get(key)
        if existing_idx is not None:
            if row["timestamp"] > deduped[existing_idx]["timestamp"]:
                deduped[existing_idx] = row
        else:
            seen[key] = len(deduped)
            deduped.append(row)
    rows = deduped
    deduplicated = len(messages) - len(rows)

    stmt = pg_insert(WhatsappMessage).values(rows)
    update_set = {col: getattr(stmt.excluded, col) for col in _MESSAGE_UPDATE_COLUMNS}
    stmt = stmt.on_conflict_do_update(
        index_elements=["chat_jid", "wa_message_id"],
        set_=update_set,
        where=(stmt.excluded.timestamp >= WhatsappMessage.timestamp),
    )
    # Canonical PostgreSQL trick: ``xmax`` is 0 for a freshly-inserted
    # row, the locking txid for an updated one. RETURNING one boolean
    # per row keeps the wire payload small.
    stmt = stmt.returning((literal_column("xmax") == 0).label("inserted"))

    result = await db.execute(stmt)
    returned = result.all()
    inserted = sum(1 for r in returned if r.inserted)
    updated = len(returned) - inserted
    skipped = len(rows) - len(returned)
    await db.flush()

    return BatchUpsertResult(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        deduplicated=deduplicated,
    )


async def list_messages(
    db: AsyncSession,
    *,
    chat_jid: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sender: str | None = None,
    contains: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[WhatsappMessage]:
    """List messages with optional filters. Default order: timestamp DESC."""
    query = select(WhatsappMessage)
    if chat_jid is not None:
        query = query.where(WhatsappMessage.chat_jid == chat_jid)
    if since is not None:
        query = query.where(WhatsappMessage.timestamp >= since)
    if until is not None:
        query = query.where(WhatsappMessage.timestamp <= until)
    if sender is not None:
        query = query.where(
            or_(
                WhatsappMessage.sender_jid == sender,
                WhatsappMessage.sender_name == sender,
            )
        )
    if contains is not None:
        # Case-insensitive substring search on body.
        query = query.where(WhatsappMessage.body.ilike(f"%{contains}%"))
    query = query.order_by(WhatsappMessage.timestamp.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_messages(
    db: AsyncSession,
    *,
    chat_jid: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sender: str | None = None,
    contains: str | None = None,
) -> int:
    """Count messages matching the same filters as ``list_messages``."""
    query = select(func.count()).select_from(WhatsappMessage)
    if chat_jid is not None:
        query = query.where(WhatsappMessage.chat_jid == chat_jid)
    if since is not None:
        query = query.where(WhatsappMessage.timestamp >= since)
    if until is not None:
        query = query.where(WhatsappMessage.timestamp <= until)
    if sender is not None:
        query = query.where(
            or_(
                WhatsappMessage.sender_jid == sender,
                WhatsappMessage.sender_name == sender,
            )
        )
    if contains is not None:
        query = query.where(WhatsappMessage.body.ilike(f"%{contains}%"))
    result = await db.execute(query)
    return result.scalar_one()
