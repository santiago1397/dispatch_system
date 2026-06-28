"""Technician CRUD routes — admin only.

The ``/dispatch/technicians`` page is the operator-facing surface for
managing the small list of techs that receive dispatched jobs. The list
is intentionally tiny (a handful of people) so the UI is a plain table
plus a create/edit form, not a paginated searchable console.

Every CRUD path delegates to ``app/repositories/technician.py`` which
follows the module-style free-function convention used elsewhere in
this project (mirror of ``app/repositories/whatsapp.py``).
"""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, DBSession
from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories import technician as technician_repo
from app.schemas.technician import (
    TechnicianCreate,
    TechnicianList,
    TechnicianRead,
    TechnicianUpdate,
)

router = APIRouter()


def _technician_to_read(tech) -> TechnicianRead:
    """Convert a Technician ORM row to the response schema."""
    return TechnicianRead.model_validate(tech)


@router.get("", response_model=TechnicianList, summary="List technicians")
async def list_technicians(
    db: DBSession,
    _admin: CurrentAdmin,
    include_inactive: bool = Query(
        default=False,
        description="Include soft-disabled (is_active=False) technicians. "
        "Defaults to False so the dispatch page only shows usable techs.",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List technicians ordered by name.

    By default only active technicians are returned — the dispatch
    dropdown should never show a tech the operator has disabled.
    Pass ``include_inactive=true`` for the admin view.
    """
    items = (
        await technician_repo.list_all(db)
        if include_inactive
        else await technician_repo.list_active(db)
    )
    total = len(items)
    # Naive in-memory pagination — the list is small by design (a handful
    # of techs). If this ever grows beyond ~50, switch to a paginated repo
    # query with a ``LIMIT``/``OFFSET`` round-trip.
    page = items[offset : offset + limit]
    return TechnicianList(items=[_technician_to_read(t) for t in page], total=total)


@router.post(
    "",
    response_model=TechnicianRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a technician",
)
async def create_technician(
    body_in: TechnicianCreate,
    db: DBSession,
    _admin: CurrentAdmin,
):
    """Create a new technician.

    Enforces a unique ``whatsapp_chat_jid`` — two techs can't share the
    same dispatch chat. The DB-level UNIQUE constraint would also catch
    this, but raising a clean 409 with a helpful message beats a raw
    IntegrityError on the second create.
    """
    if body_in.whatsapp_chat_jid:
        existing = await technician_repo.get_by_chat_jid(db, body_in.whatsapp_chat_jid)
        if existing is not None:
            raise AlreadyExistsError(
                message="A technician is already bound to that WhatsApp chat",
                details={
                    "whatsapp_chat_jid": body_in.whatsapp_chat_jid,
                    "technician_id": str(existing.id),
                },
            )
    tech = await technician_repo.create(
        db,
        name=body_in.name,
        phone_e164=body_in.phone_e164,
        whatsapp_chat_jid=body_in.whatsapp_chat_jid,
        is_active=body_in.is_active,
        notes=body_in.notes,
    )
    await db.commit()
    await db.refresh(tech)
    return _technician_to_read(tech)


@router.get(
    "/{technician_id}",
    response_model=TechnicianRead,
    summary="Get a single technician",
)
async def get_technician(
    technician_id: uuid.UUID,
    db: DBSession,
    _admin: CurrentAdmin,
):
    """Fetch a technician by id (admin view of the dispatch page)."""
    tech = await technician_repo.get_by_id(db, technician_id)
    if tech is None:
        raise NotFoundError(
            message="Technician not found",
            details={"technician_id": str(technician_id)},
        )
    return _technician_to_read(tech)


@router.patch(
    "/{technician_id}",
    response_model=TechnicianRead,
    summary="Update a technician",
)
async def update_technician(
    technician_id: uuid.UUID,
    body_in: TechnicianUpdate,
    db: DBSession,
    _admin: CurrentAdmin,
):
    """Patch a technician. ``None`` fields are left unchanged.

    Re-binding a different chat to the same technician is allowed
    (operator rotated the tech to a new dispatch group); but binding
    a chat that's already bound to a DIFFERENT technician is rejected
    with 409.
    """
    tech = await technician_repo.get_by_id(db, technician_id)
    if tech is None:
        raise NotFoundError(
            message="Technician not found",
            details={"technician_id": str(technician_id)},
        )

    if body_in.whatsapp_chat_jid:
        existing = await technician_repo.get_by_chat_jid(db, body_in.whatsapp_chat_jid)
        if existing is not None and existing.id != tech.id:
            raise AlreadyExistsError(
                message="A different technician is already bound to that WhatsApp chat",
                details={
                    "whatsapp_chat_jid": body_in.whatsapp_chat_jid,
                    "other_technician_id": str(existing.id),
                },
            )

    tech = await technician_repo.update(
        db,
        tech,
        name=body_in.name,
        phone_e164=body_in.phone_e164,
        whatsapp_chat_jid=body_in.whatsapp_chat_jid,
        is_active=body_in.is_active,
        notes=body_in.notes,
    )
    await db.commit()
    await db.refresh(tech)
    return _technician_to_read(tech)


@router.delete(
    "/{technician_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a technician (set is_active=False)",
)
async def deactivate_technician(
    technician_id: uuid.UUID,
    db: DBSession,
    _admin: CurrentAdmin,
):
    """Deactivate a technician instead of hard-deleting.

    We never hard-delete because historical ``JobLifecycleEvent`` rows
    may reference the tech indirectly (via the ``payload.technician_id``
    written by the operator-dispatch handler). Soft-delete keeps the
    audit trail intact while removing the tech from operator dropdowns.
    """
    tech = await technician_repo.get_by_id(db, technician_id)
    if tech is None:
        raise NotFoundError(
            message="Technician not found",
            details={"technician_id": str(technician_id)},
        )
    await technician_repo.update(db, tech, is_active=False)
    await db.commit()
    return None
