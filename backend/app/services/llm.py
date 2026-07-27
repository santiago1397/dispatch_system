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
import json
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

#: MiniMax is a reasoning model AND json_mode makes it deliberate over the
#: schema, so it is far slower than a non-thinking model. Measured on
#: MiniMax-M2.7 against the real prompts: JobExtraction 13-25s, the small
#: intent schemas 5-11s. (MiniMax-M2.7-highspeed is NOT faster here —
#: 20-30s on the wide schema.) The 20s first guess timed out constantly,
#: hence ``settings.MINIMAX_TIMEOUT_SECONDS``, read per call rather than
#: captured at import so the env var actually takes effect.
#: Classification runs in a FastAPI BackgroundTask after the webhook has
#: already responded, so a long ceiling costs throughput, not
#: user-visible latency.
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

#: How MiniMax is made to emit structured output. Measured against the
#: live API with MiniMax-M2.7 (see the ``llm-smoke`` gate):
#:
#: - ``json_schema`` (langchain-openai 1.x default) — OpenAI's ``.parse()``
#:   path. MiniMax documents ``tools`` but not ``response_format``, and
#:   ``.parse()`` also rejects ``extra_body`` passengers via model_kwargs.
#: - ``function_calling`` — works for small enum schemas
#:   (``TechReplyIntent``) but returns a tool call with EMPTY arguments for
#:   the 13-field ``JobExtraction``, at every temperature and up to
#:   max_tokens=8192. Schema-valid, entirely null: the worst outcome,
#:   because the extraction sites accept all-null as legitimate.
#: - ``json_mode`` alone — the model reads the message correctly but
#:   invents its own nested shape, since json_mode sends no schema.
#: - ``json_mode`` + the schema pasted into the prompt — extracts
#:   correctly and reproducibly. This is also what
#:   ``app/commands/extract_backfill.py`` settled on independently.
_MINIMAX_STRUCTURED_METHOD = "json_mode"

#: Appended to the prompt when the provider needs the schema spelled out
#: because its structured-output mode doesn't transmit one.
_JSON_MODE_SCHEMA_INSTRUCTION = (
    "\n\nRespond with ONLY a JSON object matching EXACTLY this JSON Schema. "
    "Use these exact top-level keys, with no nesting and no extra keys. "
    "Set a key to null when the value is not present.\n\n{schema}"
)

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
    #: Non-OpenAI request-body fields. MUST NOT be passed via
    #: ``model_kwargs``: langchain-openai 1.x routes structured output
    #: through ``AsyncCompletions.parse()``, which validates kwargs
    #: strictly and raises TypeError on anything it doesn't recognise.
    #: ``extra_body`` is merged into the HTTP body untouched.
    extra_body: dict = field(default_factory=dict)
    #: ``with_structured_output(method=...)``. ``None`` keeps langchain's
    #: default (``json_schema``), which is what every call site used
    #: before this module existed.
    structured_method: str | None = None
    #: Paste the JSON Schema into the prompt. Required for ``json_mode``,
    #: which transmits no schema of its own.
    schema_in_prompt: bool = False
    #: Treat a result whose every field is null as a failure and escalate.
    #: MiniMax returns a spurious all-null object on a minority of calls
    #: (observed ~1 in 6 for JobExtraction). Without this the extraction
    #: sites accept it as "the message had no fields" and write an empty
    #: job. Only ever set on a NON-final provider, so a genuinely empty
    #: message just costs one extra call to a provider that agrees.
    reject_empty: bool = False

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


def _is_all_null(result: BaseModel) -> bool:
    """True when every field came back null/blank — a non-answer."""
    return all(v is None or v == "" for v in result.model_dump().values())


def _warn_missing_primary_key_once() -> None:
    global _missing_key_warned
    if _missing_key_warned:
        return
    _missing_key_warned = True
    logger.warning(
        "llm_config LLM_PROVIDER=minimax but MINIMAX_API_KEY is empty — "
        "serving all structured extraction from the OpenAI fallback"
    )


def _minimax_spec() -> _ProviderCall:
    """The MiniMax primary. Single definition — the probe uses it too."""
    return _ProviderCall(
        provider=MINIMAX,
        model=settings.MINIMAX_MODEL,
        base_url=settings.MINIMAX_BASE_URL,
        api_key=settings.MINIMAX_API_KEY,
        temperature=_MINIMAX_TEMPERATURE,
        timeout=float(settings.MINIMAX_TIMEOUT_SECONDS),
        max_retries=_PRIMARY_MAX_RETRIES,
        top_p=_MINIMAX_TOP_P,
        extra_body={"reasoning_split": True},
        structured_method=_MINIMAX_STRUCTURED_METHOD,
        schema_in_prompt=True,
        reject_empty=True,
    )


def _openai_spec(*, base_url: str, api_key: str, temperature: float) -> _ProviderCall:
    """The OpenAI fallback. Credentials are passed in because the runtime
    path honours the ``app_settings`` DB override while the probe does not.
    """
    return _ProviderCall(
        provider=OPENAI,
        model=settings.AI_MODEL,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=_OPENAI_TIMEOUT_S,
        max_retries=_FALLBACK_MAX_RETRIES,
    )


async def _resolve_chain(db: AsyncSession, *, temperature: float) -> list[_ProviderCall]:
    """Build the ordered provider chain for one call.

    The OpenAI entry keeps honouring the ``app_settings`` DB override so
    existing runtime reconfiguration keeps working untouched.
    """
    fallback_cfg = await AppSettingsService(db).get_llm_config()
    fallback = _openai_spec(
        base_url=fallback_cfg.base_url,
        api_key=fallback_cfg.api_key,
        temperature=temperature,
    )

    if settings.LLM_PROVIDER.strip().lower() != MINIMAX:
        return [fallback]

    if not settings.MINIMAX_API_KEY:
        _warn_missing_primary_key_once()
        return [fallback]

    return [_minimax_spec(), fallback]


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
    if spec.extra_body:
        kwargs["extra_body"] = dict(spec.extra_body)
    return ChatOpenAI(**kwargs)


def _build_structured(spec: _ProviderCall, schema: type[T]):
    """Bind ``schema`` to a provider client using that provider's method."""
    client = _build_client(spec)
    if spec.structured_method is None:
        return client.with_structured_output(schema)
    return client.with_structured_output(schema, method=spec.structured_method)


def _build_prompt(spec: _ProviderCall, schema: type[T], prompt: str) -> str:
    """Append the JSON Schema when the provider's mode doesn't send one."""
    if not spec.schema_in_prompt:
        return prompt
    return prompt + _JSON_MODE_SCHEMA_INSTRUCTION.format(
        schema=json.dumps(schema.model_json_schema())
    )


async def _attempt(spec: _ProviderCall, schema: type[T], prompt: str, *, site: str) -> T:
    """Invoke one provider, retrying once on 429 when that provider allows it."""
    structured = _build_structured(spec, schema)
    final_prompt = _build_prompt(spec, schema, prompt)
    max_attempts = 2 if spec.retry_on_rate_limit else 1

    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            result = await structured.ainvoke(final_prompt)
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

        if not is_final and spec.reject_empty and _is_all_null(result):
            logger.info(
                "llm_fallback site=%s from=%s to=%s reason=empty_result",
                site,
                spec.provider,
                chain[index + 1].provider,
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


def probe_spec(provider: str, *, temperature: float = 0.0) -> _ProviderCall:
    """The spec for ONE provider, with no DB lookup and no fallback.

    Built from the same ``_minimax_spec`` / ``_openai_spec`` the runtime
    path uses, so the ``llm-smoke`` probe can never drift from what
    production does. Credentials come from env only, so this deliberately
    ignores any ``app_settings`` DB override of the OpenAI key.
    """
    if provider == MINIMAX:
        return _minimax_spec()
    if provider == OPENAI:
        return _openai_spec(
            base_url=settings.AI_BASE_URL,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
        )
    raise ValueError(f"unknown provider {provider!r}")


async def probe_invoke(
    provider: str, schema: type[T], prompt: str, *, temperature: float = 0.0
) -> T:
    """Run one prompt against ONE provider — no fallback, no retry wrapper.

    Probing a single provider in isolation is the entire point of the
    ``llm-smoke`` gate: the automatic fallback would otherwise mask a
    primary that cannot produce usable structured output.

    Goes through the same method selection and prompt augmentation as the
    runtime path, so a pass here means the real call sites will work.
    """
    spec = probe_spec(provider, temperature=temperature)
    structured = _build_structured(spec, schema)
    return await structured.ainvoke(_build_prompt(spec, schema, prompt))


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
