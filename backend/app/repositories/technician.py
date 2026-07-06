"""Repository for Technician CRUD.

Module-style free functions (mirrors ``app/repositories/whatsapp.py``).
Use ``db.flush()`` not ``commit()``; callers commit.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.technician import Technician
from app.services.address_normalizer import normalize_phone


async def list_active(db: AsyncSession) -> list[Technician]:
    """Return active technicians ordered by name."""
    query = select(Technician).where(Technician.is_active.is_(True)).order_by(Technician.name)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_all(db: AsyncSession) -> list[Technician]:
    """Return all technicians (active + inactive) ordered by name."""
    query = select(Technician).order_by(Technician.name)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, technician_id: uuid.UUID) -> Technician | None:
    """Get a technician by id."""
    return await db.get(Technician, technician_id)


async def get_by_chat_jid(db: AsyncSession, chat_jid: str) -> Technician | None:
    """Get a technician by their dispatch chat JID.

    Returns ``None`` if no Technician owns that chat, or if the chat
    isn't tagged ``tech_dispatch`` yet (the operator must do that from
    ``/dispatch/chat-roles`` before the dispatch detector can match).
    """
    query = select(Technician).where(Technician.whatsapp_chat_jid == chat_jid)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_by_phone_e164(db: AsyncSession, phone: str | None) -> Technician | None:
    """Get a technician by phone, matched on the canonical 10-digit form.

    Normalizes the input the same way ``phone_e164`` is stored (via
    ``normalize_phone``) so an OpenPhone webhook ``from_number`` / ``to``
    value in any format resolves to the technician's dispatch chat.
    Returns ``None`` when the input can't be normalized to 10 digits or
    no technician owns that number.
    """
    normalized = normalize_phone(phone)
    if normalized is None:
        return None
    query = select(Technician).where(Technician.phone_e164 == normalized)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    name: str,
    phone_e164: str | None = None,
    whatsapp_chat_jid: str | None = None,
    is_active: bool = True,
    notes: str | None = None,
) -> Technician:
    """Insert a new Technician."""
    technician = Technician(
        name=name,
        # Canonicalize to the 10-digit form used as the OpenPhone match key.
        # Keep the raw value if it can't be normalized so no input is lost.
        phone_e164=(normalize_phone(phone_e164) or phone_e164) if phone_e164 else None,
        whatsapp_chat_jid=whatsapp_chat_jid,
        is_active=is_active,
        notes=notes,
    )
    db.add(technician)
    await db.flush()
    await db.refresh(technician)
    return technician


async def update(
    db: AsyncSession,
    technician: Technician,
    *,
    name: str | None = None,
    phone_e164: str | None = None,
    whatsapp_chat_jid: str | None = None,
    is_active: bool | None = None,
    notes: str | None = None,
) -> Technician:
    """Update mutable fields on a Technician.

    Passing ``None`` for an argument leaves the column unchanged. Use
    ``whatsapp_chat_jid=''`` (empty string) to disambiguate "not
    provided" vs "explicitly nulled".
    """
    if name is not None:
        technician.name = name
    if phone_e164 is not None:
        # Canonicalize to the 10-digit match key; empty string clears it.
        technician.phone_e164 = (normalize_phone(phone_e164) or phone_e164) or None
    if whatsapp_chat_jid is not None:
        technician.whatsapp_chat_jid = whatsapp_chat_jid or None
    if is_active is not None:
        technician.is_active = is_active
    if notes is not None:
        technician.notes = notes
    db.add(technician)
    await db.flush()
    await db.refresh(technician)
    return technician
