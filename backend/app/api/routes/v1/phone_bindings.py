"""Operator-curated phone -> company bindings.

Configuration surface for the optional third classification tier. The
classifier consumes the same data via ``phone_binding_repo`` directly;
these routes only exist for the dashboard's configuration page.

Operator-only: no service-account access (the extension never needs to
read or mutate bindings).
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.company_phone_binding import (
    PhoneBindingCreate,
    PhoneBindingList,
    PhoneBindingRead,
    PhoneBindingSuggestion,
    PhoneBindingSuggestionList,
)
from app.services.company_phone_binding import PhoneBindingService

router = APIRouter()


def _to_read(binding) -> PhoneBindingRead:
    """Flatten a binding ORM row into the response schema."""
    return PhoneBindingRead(
        id=binding.id,
        phone_e164=binding.phone_e164,
        company_id=binding.company_id,
        company_name=binding.company.name,
        company_display_name=binding.company.display_name,
        note=binding.note,
        created_at=binding.created_at,
    )


@router.get("", response_model=PhoneBindingList, summary="List phone bindings")
async def list_phone_bindings(
    db: DBSession,
    _user: CurrentUser,
):
    """Return every operator-curated binding, newest first."""
    service = PhoneBindingService(db)
    bindings = await service.list_all()
    items = [_to_read(b) for b in bindings]
    return PhoneBindingList(items=items, total=len(items))


@router.post(
    "",
    response_model=PhoneBindingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a phone binding",
)
async def create_phone_binding(
    payload: PhoneBindingCreate,
    db: DBSession,
    user: CurrentUser,
):
    """Bind a phone number to a company.

    The phone is normalized to 10 US digits server-side; duplicates
    return 409.
    """
    service = PhoneBindingService(db)
    binding = await service.create(
        phone=payload.phone,
        company_id=payload.company_id,
        note=payload.note,
        created_by_user_id=user.id,
    )
    return _to_read(binding)


@router.delete(
    "/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a phone binding",
)
async def delete_phone_binding(
    binding_id: UUID,
    db: DBSession,
    _user: CurrentUser,
):
    """Remove a binding. 404 when the ID is unknown."""
    service = PhoneBindingService(db)
    await service.delete(binding_id)


@router.get(
    "/suggestions",
    response_model=PhoneBindingSuggestionList,
    summary="Suggested phone bindings from observed regex matches",
)
async def list_phone_binding_suggestions(
    db: DBSession,
    _user: CurrentUser,
):
    """Aggregate of OpenPhone numbers that regex-classified to a company
    and are not yet bound. Ranked by hit count."""
    service = PhoneBindingService(db)
    rows = await service.list_suggestions()
    items = [PhoneBindingSuggestion(**row) for row in rows]
    return PhoneBindingSuggestionList(items=items, total=len(items))
