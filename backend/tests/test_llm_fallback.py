"""Tests for the dual-provider LLM fallback helper.

These exercise ``app.services.llm.ainvoke_structured`` in isolation by
replacing ``_build_client``, so no network or LangChain machinery is
involved. The five production call sites are covered by their own tests
and are unaffected — they patch above this layer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from app.services import llm as llm_module
from app.services.app_settings import LLMConfig
from app.services.llm import (
    MINIMAX,
    OPENAI,
    ainvoke_structured,
    build_probe_structured,
    describe_chain,
)


class Sample(BaseModel):
    """Minimal structured-output schema for the helper under test."""

    value: str
    confidence: float = 1.0


class FakeAPIError(Exception):
    """Stand-in for a generic provider error."""


class FakeAPITimeoutError(Exception):
    """Name deliberately contains 'Timeout' — that's how the reason is derived."""


class FakeRateLimitError(Exception):
    """429. Detected via ``status_code`` rather than by class identity."""

    status_code = 429


def _validation_error() -> ValidationError:
    try:
        Sample()  # type: ignore[call-arg]
    except ValidationError as exc:
        return exc
    raise AssertionError("Sample() unexpectedly validated")  # pragma: no cover


class _FakeStructured:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def ainvoke(self, prompt: str):
        self.calls += 1
        if not self._outcomes:
            raise AssertionError("provider invoked more times than scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list) -> None:
        self.structured = _FakeStructured(outcomes)
        self.schema = None
        self.method = None

    def with_structured_output(self, schema, method=None, **kwargs):
        self.schema = schema
        self.method = method
        return self.structured


@pytest.fixture
def wire(monkeypatch):
    """Script per-provider outcomes and capture the specs actually built.

    Usage::

        specs, clients = wire({MINIMAX: [exc], OPENAI: [Sample(value="x")]})
    """
    monkeypatch.setattr(llm_module, "_RATE_LIMIT_BACKOFF_S", 0)
    monkeypatch.setattr(llm_module, "_missing_key_warned", False)

    def _install(outcomes_by_provider: dict[str, list]):
        clients = {p: _FakeClient(o) for p, o in outcomes_by_provider.items()}
        specs = []

        def _build(spec):
            specs.append(spec)
            if spec.provider not in clients:
                raise AssertionError(f"unexpected provider contacted: {spec.provider}")
            return clients[spec.provider]

        monkeypatch.setattr(llm_module, "_build_client", _build)

        svc = MagicMock()
        svc.return_value.get_llm_config = AsyncMock(
            return_value=LLMConfig(
                api_key="sk-openai",
                base_url="https://api.openai.com/v1",
                api_key_source="env",
                base_url_source="env",
            )
        )
        monkeypatch.setattr(llm_module, "AppSettingsService", svc)
        return specs, clients

    return _install


@pytest.fixture
def minimax_primary(monkeypatch):
    """LLM_PROVIDER=minimax with a key present."""
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "minimax")
    monkeypatch.setattr(llm_module.settings, "MINIMAX_API_KEY", "sk-cp-test")
    monkeypatch.setattr(llm_module.settings, "MINIMAX_MODEL", "MiniMax-M2.7")
    monkeypatch.setattr(llm_module.settings, "MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    monkeypatch.setattr(llm_module.settings, "AI_MODEL", "gpt-4.1")


@pytest.fixture
def openai_only(monkeypatch):
    """Default wiring — historical single-provider behaviour."""
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm_module.settings, "MINIMAX_API_KEY", "sk-cp-test")
    monkeypatch.setattr(llm_module.settings, "AI_MODEL", "gpt-4.1")


async def _run(prompt="p", **kwargs):
    return await ainvoke_structured(AsyncMock(), Sample, prompt, site="test", **kwargs)


# --- happy path -------------------------------------------------------


@pytest.mark.anyio
async def test_primary_success_never_touches_fallback(wire, minimax_primary):
    specs, _ = wire({MINIMAX: [Sample(value="ok")]})

    result = await _run()

    assert result.value == "ok"
    assert [s.provider for s in specs] == [MINIMAX]


@pytest.mark.anyio
async def test_minimax_uses_vendor_sampling_and_no_internal_retries(wire, minimax_primary):
    specs, _ = wire({MINIMAX: [Sample(value="ok")]})

    await _run(temperature=0.0)

    spec = specs[0]
    assert spec.temperature == 1.0, "MiniMax must use its vendor-specified temperature"
    assert spec.top_p == 0.95
    assert spec.max_retries == 0, "LangChain's default of 2 would triple primary latency"
    assert spec.timeout == 20.0
    # reasoning_split MUST travel via extra_body. Via model_kwargs it reaches
    # AsyncCompletions.parse(), which rejects unknown kwargs with TypeError.
    assert spec.extra_body == {"reasoning_split": True}
    # MiniMax documents `tools` but not response_format/json_schema, and
    # langchain-openai 1.x defaults to json_schema.
    assert spec.structured_method == "function_calling"


@pytest.mark.anyio
async def test_fallback_keeps_caller_temperature_and_retries(wire, minimax_primary):
    specs, _ = wire({MINIMAX: [FakeAPIError("down")], OPENAI: [Sample(value="ok")]})

    await _run(temperature=0.1)

    fallback = specs[1]
    assert fallback.provider == OPENAI
    assert fallback.temperature == 0.1
    assert fallback.top_p is None
    assert fallback.extra_body == {}
    assert fallback.max_retries == 2
    assert fallback.model == "gpt-4.1"
    # None keeps langchain's default, i.e. exactly what every call site
    # did before this module existed.
    assert fallback.structured_method is None


# --- hard failures fall back -----------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        FakeAPIError("boom"),
        FakeAPITimeoutError("slow"),
        _validation_error(),
        None,  # provider returned no structured output at all
    ],
    ids=["api_error", "timeout", "validation_error", "no_structured_output"],
)
@pytest.mark.anyio
async def test_hard_failure_falls_back_to_openai(wire, minimax_primary, failure):
    specs, _ = wire({MINIMAX: [failure], OPENAI: [Sample(value="rescued")]})

    result = await _run()

    assert result.value == "rescued"
    assert [s.provider for s in specs] == [MINIMAX, OPENAI]


@pytest.mark.anyio
async def test_http_5xx_falls_back(wire, minimax_primary):
    err = FakeAPIError("server error")
    err.status_code = 503
    specs, _ = wire({MINIMAX: [err], OPENAI: [Sample(value="rescued")]})

    assert (await _run()).value == "rescued"
    assert [s.provider for s in specs] == [MINIMAX, OPENAI]


# --- 429 handling -----------------------------------------------------


@pytest.mark.anyio
async def test_rate_limit_retries_once_then_succeeds(wire, minimax_primary):
    specs, clients = wire({MINIMAX: [FakeRateLimitError(), Sample(value="second try")]})

    result = await _run()

    assert result.value == "second try"
    assert clients[MINIMAX].structured.calls == 2
    assert [s.provider for s in specs] == [MINIMAX], "must not reach OpenAI"


@pytest.mark.anyio
async def test_rate_limit_twice_falls_back(wire, minimax_primary):
    specs, clients = wire(
        {
            MINIMAX: [FakeRateLimitError(), FakeRateLimitError()],
            OPENAI: [Sample(value="rescued")],
        }
    )

    assert (await _run()).value == "rescued"
    assert clients[MINIMAX].structured.calls == 2, "exactly one retry, not more"
    assert [s.provider for s in specs] == [MINIMAX, OPENAI]


@pytest.mark.anyio
async def test_fallback_does_not_retry_on_rate_limit(wire, minimax_primary):
    """Only the primary re-attempts; the fallback fails straight through."""
    _, clients = wire({MINIMAX: [FakeAPIError("down")], OPENAI: [FakeRateLimitError()]})

    with pytest.raises(FakeRateLimitError):
        await _run()

    assert clients[OPENAI].structured.calls == 1


# --- caller-driven quality escalation ---------------------------------


@pytest.mark.anyio
async def test_rejected_primary_result_escalates(wire, minimax_primary):
    specs, _ = wire(
        {
            MINIMAX: [Sample(value="maybe", confidence=0.2)],
            OPENAI: [Sample(value="sure", confidence=0.9)],
        }
    )

    result = await _run(accept=lambda r: r.confidence >= 0.5)

    assert result.value == "sure"
    assert [s.provider for s in specs] == [MINIMAX, OPENAI]


@pytest.mark.anyio
async def test_rejected_fallback_result_is_still_returned(wire, minimax_primary):
    """The final provider's answer always reaches the caller.

    The call site applies its own threshold afterwards, exactly as it did
    before this helper existed.
    """
    wire(
        {
            MINIMAX: [Sample(value="a", confidence=0.1)],
            OPENAI: [Sample(value="b", confidence=0.2)],
        }
    )

    result = await _run(accept=lambda r: r.confidence >= 0.5)

    assert result.value == "b"


# --- total failure ----------------------------------------------------


@pytest.mark.anyio
async def test_both_providers_fail_raises_final_error(wire, minimax_primary):
    wire({MINIMAX: [FakeAPIError("primary")], OPENAI: [FakeAPIError("fallback")]})

    with pytest.raises(FakeAPIError, match="fallback"):
        await _run()


# --- provider selection / guards --------------------------------------


@pytest.mark.anyio
async def test_openai_provider_never_contacts_minimax(wire, openai_only):
    specs, _ = wire({OPENAI: [Sample(value="ok")]})

    assert (await _run()).value == "ok"
    assert [s.provider for s in specs] == [OPENAI]


@pytest.mark.anyio
async def test_openai_provider_has_no_fallback(wire, openai_only):
    """With a single-provider chain the error propagates, as it always has."""
    wire({OPENAI: [FakeAPIError("down")]})

    with pytest.raises(FakeAPIError):
        await _run()


@pytest.mark.anyio
async def test_minimax_requested_but_key_missing_degrades(wire, minimax_primary, monkeypatch):
    monkeypatch.setattr(llm_module.settings, "MINIMAX_API_KEY", "")
    specs, _ = wire({OPENAI: [Sample(value="ok")]})

    assert (await _run()).value == "ok"
    assert [s.provider for s in specs] == [OPENAI]


@pytest.mark.anyio
async def test_provider_name_is_case_and_space_tolerant(wire, minimax_primary, monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "  MiniMax ")
    specs, _ = wire({MINIMAX: [Sample(value="ok")]})

    await _run()

    assert [s.provider for s in specs] == [MINIMAX]


# --- real client wiring -----------------------------------------------
# These use the genuine _build_client / _build_structured rather than the
# `wire` fake. The rest of the suite stubs the factory out, so without
# these nothing verifies that what we hand LangChain is actually valid.


def test_reasoning_split_goes_in_extra_body_not_model_kwargs(minimax_primary):
    """Regression: model_kwargs reaches AsyncCompletions.parse().

    langchain-openai 1.x routes structured output through .parse(), which
    validates kwargs strictly and raised
    ``TypeError: unexpected keyword argument 'reasoning_split'`` on every
    single call. extra_body is merged into the HTTP body untouched.
    """
    client = llm_module._build_client(llm_module._minimax_spec())

    assert client.extra_body == {"reasoning_split": True}
    assert not client.model_kwargs


def test_minimax_structured_output_uses_function_calling(minimax_primary):
    """MiniMax documents `tools`, not response_format/json_schema.

    Building the runnable also proves LangChain accepts the method name;
    an invalid one raises at bind time.
    """
    runnable = llm_module._build_structured(llm_module._minimax_spec(), Sample)
    assert runnable is not None


def test_probe_builds_for_both_providers(minimax_primary):
    """The probe shares the runtime specs, so it cannot drift from prod."""
    for provider in (MINIMAX, OPENAI):
        assert build_probe_structured(provider, Sample) is not None


# --- startup banner ---------------------------------------------------


def test_describe_chain_openai_only(openai_only):
    assert describe_chain() == "primary=openai model=gpt-4.1 (no fallback)"


def test_describe_chain_dual(minimax_primary):
    text = describe_chain()
    assert "primary=minimax model=MiniMax-M2.7" in text
    assert "fallback=openai model=gpt-4.1" in text


def test_describe_chain_flags_missing_key(minimax_primary, monkeypatch):
    monkeypatch.setattr(llm_module.settings, "MINIMAX_API_KEY", "")
    assert "MINIMAX_API_KEY empty" in describe_chain()
