"""Schemas for runtime-overridable app settings."""

from typing import Literal

from pydantic import Field

from app.schemas.base import BaseSchema

Source = Literal["db", "env"]


class LLMApiKeyView(BaseSchema):
    """Masked view of the LLM API key — never returns the plaintext."""

    is_set: bool
    last4: str | None = None
    source: Source


class LLMBaseUrlView(BaseSchema):
    """Plaintext view of the LLM base URL (not a secret)."""

    value: str
    source: Source


class LLMConfigRead(BaseSchema):
    """Current resolved LLM configuration as returned to the admin UI."""

    llm_api_key: LLMApiKeyView
    llm_base_url: LLMBaseUrlView


class LLMConfigUpdate(BaseSchema):
    """Body for partial update of the LLM config.

    Omit a field to leave it unchanged. Use the DELETE endpoint to clear
    overrides and fall back to .env.
    """

    llm_api_key: str | None = Field(
        default=None, min_length=1, description="Plaintext LLM API key override"
    )
    llm_base_url: str | None = Field(
        default=None, min_length=1, max_length=512, description="LLM base URL override"
    )
