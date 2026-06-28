"""Company schemas for API responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanyRead(BaseModel):
    """Company response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str | None = None
    pattern_type: str
    identification_patterns: list[dict] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None


class CompanyList(BaseModel):
    """Paginated list of companies."""

    items: list[CompanyRead]
    total: int
