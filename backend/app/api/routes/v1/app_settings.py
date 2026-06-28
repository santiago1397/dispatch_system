"""Application settings routes — admin-only LLM config override.

Endpoints:
- GET /settings/llm   → current resolved LLM config (key masked)
- PUT /settings/llm   → partial update of overrides
- DELETE /settings/llm → clear overrides; fall back to .env
"""

from fastapi import APIRouter, status

from app.api.deps import AppSettingsSvc, CurrentAdmin
from app.schemas.app_settings import (
    LLMApiKeyView,
    LLMBaseUrlView,
    LLMConfigRead,
    LLMConfigUpdate,
)

router = APIRouter()


def _mask(api_key: str) -> str | None:
    """Return the last 4 characters of `api_key`, or None if it's too short."""
    if not api_key:
        return None
    return api_key[-4:] if len(api_key) >= 4 else None


async def _build_view(service: AppSettingsSvc) -> LLMConfigRead:
    config = await service.get_llm_config()
    return LLMConfigRead(
        llm_api_key=LLMApiKeyView(
            is_set=bool(config.api_key),
            last4=_mask(config.api_key),
            source=config.api_key_source,
        ),
        llm_base_url=LLMBaseUrlView(
            value=config.base_url,
            source=config.base_url_source,
        ),
    )


@router.get("/llm", response_model=LLMConfigRead)
async def get_llm_settings(
    service: AppSettingsSvc,
    _admin: CurrentAdmin,
):
    """Return the currently-resolved LLM config (key masked)."""
    return await _build_view(service)


@router.put("/llm", response_model=LLMConfigRead)
async def update_llm_settings(
    payload: LLMConfigUpdate,
    service: AppSettingsSvc,
    admin: CurrentAdmin,
):
    """Update the LLM override. Omit a field to leave it unchanged."""
    await service.update(
        llm_api_key=payload.llm_api_key if payload.llm_api_key is not None else ...,
        llm_base_url=payload.llm_base_url if payload.llm_base_url is not None else ...,
        user_id=admin.id,
    )
    return await _build_view(service)


@router.delete("/llm", response_model=LLMConfigRead, status_code=status.HTTP_200_OK)
async def reset_llm_settings(
    service: AppSettingsSvc,
    admin: CurrentAdmin,
):
    """Clear all LLM overrides; subsequent calls use the .env values."""
    await service.reset(user_id=admin.id)
    return await _build_view(service)
