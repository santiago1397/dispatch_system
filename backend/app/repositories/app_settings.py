"""AppSettings repository — singleton row (id=1) holding runtime config overrides."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.app_settings import AppSettings

_SINGLETON_ID = 1


async def get(db: AsyncSession) -> AppSettings | None:
    """Fetch the singleton settings row, or None if it doesn't exist yet."""
    return await db.get(AppSettings, _SINGLETON_ID)


async def upsert(
    db: AsyncSession,
    *,
    llm_api_key: str | None | type(...) = ...,
    llm_base_url: str | None | type(...) = ...,
    updated_by_user_id: UUID | None = None,
) -> AppSettings:
    """Insert the singleton row if missing, else update only provided fields.

    Pass `...` (the Ellipsis sentinel) to leave a field unchanged. Pass `None`
    to explicitly clear it (fall back to .env).
    """
    row = await get(db)
    if row is None:
        row = AppSettings(
            id=_SINGLETON_ID,
            llm_api_key=None if llm_api_key is ... else llm_api_key,
            llm_base_url=None if llm_base_url is ... else llm_base_url,
            updated_by_user_id=updated_by_user_id,
        )
        db.add(row)
    else:
        if llm_api_key is not ...:
            row.llm_api_key = llm_api_key
        if llm_base_url is not ...:
            row.llm_base_url = llm_base_url
        row.updated_by_user_id = updated_by_user_id
    await db.flush()
    await db.refresh(row)
    return row


async def clear(db: AsyncSession, *, updated_by_user_id: UUID | None = None) -> AppSettings:
    """Null out all override columns, falling back to .env for every field."""
    return await upsert(
        db,
        llm_api_key=None,
        llm_base_url=None,
        updated_by_user_id=updated_by_user_id,
    )
