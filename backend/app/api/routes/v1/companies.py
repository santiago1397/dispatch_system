"""Company routes — read-only listing for operator UI filters.

Returns the list of active companies for dropdowns (e.g., the Jobs page
filter toolbar). Operator-only — no service-account auth, since the
extension doesn't need this endpoint.
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.schemas.company import CompanyList, CompanyRead
from app.services.company import CompanyService

router = APIRouter()


@router.get("", response_model=CompanyList, summary="List active companies")
async def list_companies(
    db: DBSession,
    _user: CurrentUser,
):
    """Return all active companies, alphabetically by display name.

    The Jobs page uses this to populate the Company filter dropdown.
    """
    service = CompanyService(db)
    companies = await service.list_active()
    return CompanyList(
        items=[CompanyRead.model_validate(c) for c in companies],
        total=len(companies),
    )
