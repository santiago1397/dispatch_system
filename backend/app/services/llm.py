"""Dual-provider structured LLM invocation with automatic fallback.

Every structured extraction in the app funnels through
:func:`ainvoke_structured`, which runs a *primary* provider and falls back
to a *fallback* provider when the primary errors, rate-limits, times out,
or returns something that doesn't satisfy the caller's acceptance test.

Provider selection is driven entirely by env (no DB override for the
primary — ``app_settings`` continues to override the OpenAI fallback only):

- ``LLM_PROVIDER=openai``  (default) — single provider, byte-for-byte the
  historical behaviour. MiniMax is never contacted.
- ``LLM_PROVIDER=minimax`` — MiniMax first, OpenAI as fallback. If
  ``MINIMAX_API_KEY`` is empty we warn once and serve from OpenAI rather
  than failing, so a half-configured deploy degrades instead of breaking.

Sampling is per-provider on purpose. MiniMax's M2 series is an interleaved
*thinking* model whose model card specifies ``temperature=1.0`` /
``top_p=0.95``; driving a reasoning model at ``temperature=0`` invites
degenerate repetition. OpenAI keeps the deterministic settings the call
sites have always used. Call sites pass the temperature they want from a
*non-reasoning* model and this module remaps it for MiniMax.

``reasoning_split=True`` asks MiniMax to keep ``<think>`` reasoning out of
``content`` — the same flag ``app/commands/extract_backfill.py`` relies on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.app_settings import AppSettingsService

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MINIMAX = "minimax"
OPENAI = "openai"

#: Vendor-specified sampling for the MiniMax M2 series. See the model card:
#: https://huggingface.co/MiniMaxAI/MiniMax-M2 (temperature=1.0, top_p=0.95).
#: ``top_k`` is also recommended but is not accepted by MiniMax's
#: OpenAI-compatible endpoint, so it is not sent.
_MINIMAX_TEMPERATURE = 1.0
_MINIMAX_TOP_P = 0.95

#: MiniMax is a reasoning model, so it is slower than a comparable
#: non-thinking model. 20s is a ceiling, not a target — exceeding it means
#: we'd rather pay for OpenAI than leave a job unclassified.
_MINIMAX_TIMEOUT_S = 20.0
_OPENAI_TIMEOUT_S = 30.0

#: LangChain's ChatOpenAI defaults to ``max_retries=2``. Left alone that
#: would silently triple primary latency before our fallback ever fires,
#: so the primary gets exactly one shot. The fallback is the last line of
#: defence and keeps retries.
_PRIMARY_MAX_RETRIES = 0
_FALLBACK_MAX_RETRIES = 2

#: MiniMax coding-plan keys rate-limit hard under concurrency. A 429 is
#: transient and cheap to re-attempt once before spending OpenAI tokens.
_RATE_LIMIT_BACKOFF_S = 2.0

_missing_key_warned = False


class LLMStructuredOutputError(RuntimeError):
    """Provider responded but produced no parseable structured output.

    ``with_structured_output`` yields ``None`` when the model declines to
    emit a tool call. That is a real possibility for MiniMax, whose native
    tool-call format is a ``<minimax:tool_call>`` XML envelope normalised
    server-side rather than OpenAI-native JSON — so we treat it as a
    failure worth falling back on rather than a valid empty answer.
    """


@dataclass(frozen=True)
class _ProviderCall:
    """Everything needed to make one provider attempt."""

    provider: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    timeout: float
    max_retries: int
    top_p: float | None = None
    model_kwargs: dict = field(default_factory=dict)

    @property
    def retry_on_rate_limit(self) -> bool:
        """Only the MiniMax primary re-attempts; the fallback just fails."""
        return self.provider == MINIMAX


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_rate_limit(exc: BaseException) -> bool:
    """Detect a 429 without importing the openai package directly."""
    if type(exc).__name__ == "RateLimitError":
        return True
    return _status_code(exc) == 429


def _failure_reason(exc: BaseException) -> str:
    """Short, greppable label for why an attempt failed."""
    if _is_rate_limit(exc):
        return "rate_limit"
    if isinstance(exc, LLMStructuredOutputError):
        return "no_structured_output"
    if isinstance(exc, ValidationError):
        return "validation_error"
    name = type(exc).__name__
    if isinstance(exc, asyncio.TimeoutError) or "Timeout" in name:
        return "timeout"
    status = _status_code(exc)
    if status is not None:
        return f"http_{status}"
    return name


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _warn_missing_primary_key_once() -> None:
    global _missing_key_warned
    if _missing_key_warned:
        return
    _missing_key_warned = True
    logger.warning(
        "llm_config LLM_PROVIDER=minimax but MINIMAX_API_KEY is empty — "
        "serving all structured extraction from the OpenAI fallback"
    )


async def _resolve_chain(db: AsyncSession, *, temperature: float) -> list[_ProviderCall]:
    """Build the ordered provider chain for one call.

    The OpenAI entry keeps honouring the ``app_settings`` DB override so
    existing runtime reconfiguration keeps working untouched.
    """
    fallback_cfg = await AppSettingsService(db).get_llm_config()
    fallback = _ProviderCall(
        provider=OPENAI,
        model=settings.AI_MODEL,
        base_url=fallback_cfg.base_url,
        api_key=fallback_cfg.api_key,
        temperature=temperature,
        timeout=_OPENAI_TIMEOUT_S,
        max_retries=_FALLBACK_MAX_RETRIES,
    )

    if settings.LLM_PROVIDER.strip().lower() != MINIMAX:
        return [fallback]

    if not settings.MINIMAX_API_KEY:
        _warn_missing_primary_key_once()
        return [fallback]

    primary = _ProviderCall(
        provider=MINIMAX,
        model=settings.MINIMAX_MODEL,
        base_url=settings.MINIMAX_BASE_URL,
        api_key=settings.MINIMAX_API_KEY,
        temperature=_MINIMAX_TEMPERATURE,
        timeout=_MINIMAX_TIMEOUT_S,
        max_retries=_PRIMARY_MAX_RETRIES,
        top_p=_MINIMAX_TOP_P,
        model_kwargs={"reasoning_split": True},
    )
    return [primary, fallback]


def _build_client(spec: _ProviderCall) -> ChatOpenAI:
    kwargs: dict = {
        "model": spec.model,
        "temperature": spec.temperature,
        "base_url": spec.base_url,
        "api_key": spec.api_key,
        "timeout": spec.timeout,
        "max_retries": spec.max_retries,
    }
    if spec.top_p is not None:
        kwargs["top_p"] = spec.top_p
    if spec.model_kwargs:
        kwargs["model_kwargs"] = dict(spec.model_kwargs)
    return ChatOpenAI(**kwargs)


async def _attempt(spec: _ProviderCall, schema: type[T], prompt: str, *, site: str) -> T:
    """Invoke one provider, retrying once on 429 when that provider allows it."""
    structured = _build_client(spec).with_structured_output(schema)
    max_attempts = 2 if spec.retry_on_rate_limit else 1

    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            result = await structured.ainvoke(prompt)
            if result is None:
                raise LLMStructuredOutputError(
                    f"{spec.provider} returned no structured output for {schema.__name__}"
                )
        except Exception as exc:
            reason = _failure_reason(exc)
            logger.warning(
                "llm_call site=%s provider=%s model=%s outcome=error "
                "reason=%s latency_ms=%d attempt=%d",
                site,
                spec.provider,
                spec.model,
                reason,
                _elapsed_ms(started),
                attempt,
            )
            if _is_rate_limit(exc) and attempt < max_attempts:
                await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                continue
            raise

        logger.info(
            "llm_call site=%s provider=%s model=%s outcome=ok latency_ms=%d attempt=%d",
            site,
            spec.provider,
            spec.model,
            _elapsed_ms(started),
            attempt,
        )
        return result

    # Unreachable: the loop either returns or raises.
    raise AssertionError("attempt loop exited without result")  # pragma: no cover


async def ainvoke_structured(
    db: AsyncSession,
    schema: type[T],
    prompt: str,
    *,
    site: str,
    temperature: float = 0.0,
    accept: Callable[[T], bool] | None = None,
) -> T:
    """Run ``prompt`` against the provider chain and return a ``schema`` instance.

    :param site: short identifier for logs, e.g. ``"extract_fields"``.
    :param temperature: the temperature to use for a *non-reasoning*
        provider. MiniMax overrides this with its vendor-specified value.
    :param accept: optional quality gate applied to a non-final provider's
        result. Returning ``False`` escalates to the next provider — used
        by company classification to send low-confidence matches to the
        stronger model. The final provider's result is always returned,
        so a rejected fallback result still reaches the caller and the
        call site applies its own thresholds as before.

    Raises whatever the final provider raised if every provider fails.
    Call sites deliberately keep their existing error semantics.
    """
    chain = await _resolve_chain(db, temperature=temperature)

    last_exc: Exception | None = None
    for index, spec in enumerate(chain):
        is_final = index == len(chain) - 1
        try:
            result = await _attempt(spec, schema, prompt, site=site)
        except Exception as exc:
            last_exc = exc
            if is_final:
                raise
            logger.info(
                "llm_fallback site=%s from=%s to=%s reason=%s",
                site,
                spec.provider,
                chain[index + 1].provider,
                _failure_reason(exc),
            )
            continue

        if not is_final and accept is not None and not accept(result):
            logger.info(
                "llm_fallback site=%s from=%s to=%s reason=rejected_by_caller",
                site,
                spec.provider,
                chain[index + 1].provider,
            )
            continue

        return result

    # Only reachable if the chain was empty, which _resolve_chain prevents.
    raise last_exc or AssertionError("empty provider chain")  # pragma: no cover


def build_probe_client(provider: str, *, temperature: float = 0.0) -> ChatOpenAI:
    """Build a client for ONE provider, with no DB lookup and no fallback.

    Used by the ``llm-smoke`` diagnostic. Probing a single provider in
    isolation is the entire point — the automatic fallback would otherwise
    mask a primary that cannot produce structured output at all.

    Credentials come from env only, so this deliberately ignores any
    ``app_settings`` DB override of the OpenAI key.
    """
    if provider == MINIMAX:
        spec = _ProviderCall(
            provider=MINIMAX,
            model=settings.MINIMAX_MODEL,
            base_url=settings.MINIMAX_BASE_URL,
            api_key=settings.MINIMAX_API_KEY,
            temperature=_MINIMAX_TEMPERATURE,
            timeout=_MINIMAX_TIMEOUT_S,
            max_retries=_PRIMARY_MAX_RETRIES,
            top_p=_MINIMAX_TOP_P,
            model_kwargs={"reasoning_split": True},
        )
    elif provider == OPENAI:
        spec = _ProviderCall(
            provider=OPENAI,
            model=settings.AI_MODEL,
            base_url=settings.AI_BASE_URL,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            timeout=_OPENAI_TIMEOUT_S,
            max_retries=_FALLBACK_MAX_RETRIES,
        )
    else:  # pragma: no cover - guarded by click.Choice at the call site
        raise ValueError(f"unknown provider {provider!r}")
    return _build_client(spec)


def _is_degraded() -> bool:
    """True when MiniMax was requested but cannot actually be used."""
    return settings.LLM_PROVIDER.strip().lower() == MINIMAX and not settings.MINIMAX_API_KEY


def describe_chain() -> str:
    """One-line summary of provider wiring, for the startup banner."""
    if settings.LLM_PROVIDER.strip().lower() != MINIMAX:
        return f"primary=openai model={settings.AI_MODEL} (no fallback)"
    if not settings.MINIMAX_API_KEY:
        return (
            f"primary=minimax REQUESTED but MINIMAX_API_KEY empty — "
            f"serving from openai model={settings.AI_MODEL}"
        )
    return (
        f"primary=minimax model={settings.MINIMAX_MODEL} fallback=openai model={settings.AI_MODEL}"
    )


def ensure_call_logging(level: int = logging.INFO) -> None:
    """Guarantee ``llm_call`` records actually get emitted.

    ``main.py`` only calls ``logging.basicConfig()`` outside production, so
    in prod the root logger drops INFO. That would silently discard every
    ``outcome=ok`` line and leave only failures visible — making the
    fallback rate impossible to compute, which is the entire reason these
    logs exist. Attach a handler only when nothing else has configured
    logging, otherwise records propagate to the existing handlers and we'd
    emit duplicates.
    """
    logger.setLevel(level)
    if logging.getLogger().handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s"))
    logger.addHandler(handler)


def log_startup_banner() -> None:
    """State the provider wiring once at boot.

    Degraded wiring logs at WARNING so it survives production's
    WARNING-level filtering; healthy wiring stays at INFO.
    """
    text = describe_chain()
    if _is_degraded():
        logger.warning("llm_config %s", text)
    else:
        logger.info("llm_config %s", text)
