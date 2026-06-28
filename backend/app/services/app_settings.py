"""AppSettings service — resolves runtime config with .env fallback."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.app_settings import AppSettings
from app.repositories import app_settings_repo

Source = Literal["db", "env"]


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    api_key_source: Source
    base_url_source: Source


class AppSettingsService:
    """Business logic for runtime-overridable application settings."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_row(self) -> AppSettings | None:
        return await app_settings_repo.get(self.db)

    async def get_llm_config(self) -> LLMConfig:
        """Resolve LLM credentials: DB override first, .env fallback per field."""
        row = await self.get_row()
        db_key = (row.llm_api_key if row else None) or None
        db_url = (row.llm_base_url if row else None) or None
        return LLMConfig(
            api_key=db_key or settings.OPENAI_API_KEY,
            base_url=db_url or settings.AI_BASE_URL,
            api_key_source="db" if db_key else "env",
            base_url_source="db" if db_url else "env",
        )

    async def update(
        self,
        *,
        llm_api_key: str | None | type(...) = ...,
        llm_base_url: str | None | type(...) = ...,
        user_id: UUID | None = None,
    ) -> AppSettings:
        """Update provided fields. Use `...` to leave a field unchanged."""
        return await app_settings_repo.upsert(
            self.db,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            updated_by_user_id=user_id,
        )

    async def reset(self, *, user_id: UUID | None = None) -> AppSettings:
        """Clear all overrides; everything falls back to .env."""
        return await app_settings_repo.clear(self.db, updated_by_user_id=user_id)
