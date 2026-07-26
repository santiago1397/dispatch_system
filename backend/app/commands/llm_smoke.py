"""``llm-smoke`` — go/no-go probe for a single LLM provider.

Every structured extraction in this app depends on
``with_structured_output(PydanticModel)``. That is verified behaviour for
OpenAI but NOT for MiniMax: MiniMax's OpenAI-compatible endpoint documents
``tools`` but not ``response_format``/json_schema, and the model emits tool
calls in a ``<minimax:tool_call>`` XML envelope that the hosted API is
expected to normalise server-side.

So before promoting MiniMax to primary, run this against a real key::

    uv run --directory backend agents_bots cmd llm-smoke

It exercises all five production schemas against ONE provider with no
fallback, and fails loudly if any schema cannot be produced. It also
reports latency percentiles, because MiniMax-M2.7 is a reasoning model and
the runtime enforces a 20s ceiling — if p95 is near that, expect a high
fallback rate and consider ``MiniMax-M2.7-highspeed``.

Exit code is non-zero when any schema fails, so it works as a CI/deploy gate.
"""

import asyncio
import time

import click

from app.commands import command, error, info, success, warning
from app.core.config import settings
from app.schemas.dispatch_job import (
    ClosingExtraction,
    CompanyClassification,
    CompanyRelayIntent,
    JobExtraction,
    TechReplyIntent,
)
from app.services.llm import MINIMAX, OPENAI, probe_invoke

#: One representative prompt per production schema. These mirror the shape
#: of the real prompts (short instruction + a realistic dispatch message)
#: without duplicating them — the goal is schema conformance, not
#: extraction accuracy.
_SAMPLE_JOB = (
    "New job for Speedy Locksmith\n"
    "Customer: Jane Doe 312-555-0147\n"
    "Address: 1425 W Belmont Ave, Chicago, IL 60657\n"
    "Job: House Lockout\n"
    "Date: 7/10/2026  Hours: 12:00 PM to 2:00 PM\n"
    "Total: $185 cash, parts $20\n"
    "Tech: Marcus"
)

_SAMPLE_CLOSING = (
    "Dispatch closing\n"
    "1425 W Belmont Ave, Chicago IL\n"
    "Customer 312-555-0147\n"
    "Estimate was $185\n"
    "FINAL: collected $210 cash, parts $25, tip $15. Warranty 90 days."
)

#: (site, schema, prompt, expected) where ``expected`` maps a field name to
#: a substring its value must contain, case-insensitively.
#:
#: Checking CONTENT and not just schema conformance is essential. MiniMax
#: via ``function_calling`` happily returns a perfectly valid, entirely
#: null JobExtraction — and the extraction call sites accept all-null as
#: legitimate, so nothing downstream would ever notice. A schema-only gate
#: reports that as PASS and lets empty jobs reach the database.
PROBES: list[tuple[str, type, str, dict[str, str]]] = [
    (
        "classify_company",
        CompanyClassification,
        "You are a dispatch message classifier. Known companies: "
        '["Speedy Locksmith", "Windy City Garage"]\n\n'
        f"Message:\n{_SAMPLE_JOB}\n\n"
        "Respond with the best-matching company name, your confidence (0-1), "
        "and reasoning. If no company matches, return null for company_name.",
        {"company_name": "Speedy Locksmith"},
    ),
    (
        "extract_fields",
        JobExtraction,
        "You are a dispatch data extractor. Extract the structured fields "
        f"from this job dispatch message.\n\nMessage:\n{_SAMPLE_JOB}\n\n"
        "Only extract values clearly present. Set anything absent to null.",
        {
            "address": "Belmont",
            "job_type": "Lockout",
            "total": "185",
            "tech_name": "Marcus",
            "customer_name": "Jane",
        },
    ),
    (
        "extract_closing",
        ClosingExtraction,
        "You are a dispatch CLOSING extractor. Return the FINAL payment "
        "information, never the earlier estimate.\n\n"
        f"Message:\n{_SAMPLE_CLOSING}",
        # 210 not 185 — proves it took the final total, not the estimate.
        {"total": "210", "payment_method": "cash"},
    ),
    (
        "company_relay_intent",
        CompanyRelayIntent,
        "You are parsing a short reply an operator sent back to a dispatch "
        "company about a job that is still open. Classify into exactly one "
        'of two codes.\n\nMessage:\n"Customer never answered, left a '
        'voicemail, still trying"',
        {"intent": "no_answer_follow_up"},
    ),
    (
        "tech_reply_intent",
        TechReplyIntent,
        "You are parsing a short reply from a technician about a dispatched "
        "job. Classify the intent into exactly one code.\n\n"
        'Message:\n"On my way, should be there in 20"',
        {"intent": "in_progress"},
    ),
]


def _percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile. Small sample sizes make interpolation noise."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


async def _probe_once(
    provider: str, schema: type, prompt: str, expected: dict[str, str]
) -> tuple[bool, str, int]:
    """Return (ok, detail, latency_ms) for one schema against one provider."""
    started = time.monotonic()
    try:
        result = await probe_invoke(provider, schema, prompt)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", int((time.monotonic() - started) * 1000)
    latency = int((time.monotonic() - started) * 1000)

    if result is None:
        return False, "returned no structured output", latency
    if not isinstance(result, schema):
        return False, f"returned {type(result).__name__}, expected {schema.__name__}", latency

    # Content check. Schema conformance alone is not enough — see PROBES.
    data = result.model_dump()
    wrong = []
    for field, want in expected.items():
        got = data.get(field)
        if got is None:
            wrong.append(f"{field}=null (expected ~{want!r})")
        elif want.lower() not in str(got).lower():
            wrong.append(f"{field}={str(got)[:40]!r} (expected ~{want!r})")
    if wrong:
        return False, "wrong content: " + "; ".join(wrong), latency

    return True, result.model_dump_json()[:120], latency


async def _run(provider: str, runs: int) -> int:
    info(f"Probing provider={provider} runs={runs} per schema")
    if provider == MINIMAX:
        info(f"  model={settings.MINIMAX_MODEL} base_url={settings.MINIMAX_BASE_URL}")
    else:
        info(f"  model={settings.AI_MODEL} base_url={settings.AI_BASE_URL}")
    info("")

    all_latencies: list[int] = []
    failed: list[str] = []

    for site, schema, prompt, expected in PROBES:
        latencies: list[int] = []
        errors: list[str] = []
        sample = ""
        for _ in range(runs):
            ok, detail, latency = await _probe_once(provider, schema, prompt, expected)
            latencies.append(latency)
            if ok:
                sample = sample or detail
            else:
                errors.append(detail)

        all_latencies.extend(latencies)
        passed = runs - len(errors)
        label = f"{site:<22} {schema.__name__:<22}"
        if errors:
            failed.append(site)
            error(f"FAIL {label} {passed}/{runs} ok")
            for detail in dict.fromkeys(errors):
                error(f"       {detail}")
        else:
            spread = f"{min(latencies)}-{max(latencies)}ms"
            success(f"PASS {label} {passed}/{runs} ok  {spread}")
            info(f"       {sample}")

    info("")
    if all_latencies:
        info(
            f"latency p50={_percentile(all_latencies, 50)}ms "
            f"p95={_percentile(all_latencies, 95)}ms "
            f"max={max(all_latencies)}ms  (runtime ceiling is 20000ms)"
        )
        if provider == MINIMAX and _percentile(all_latencies, 95) > 15000:
            warning(
                "p95 is close to the 20s timeout — expect frequent fallback to "
                "OpenAI. Consider MINIMAX_MODEL=MiniMax-M2.7-highspeed."
            )

    if failed:
        error(f"\n{len(failed)}/{len(PROBES)} schemas FAILED: {', '.join(failed)}")
        error("Do not set LLM_PROVIDER=minimax until this passes.")
        return 1

    success(f"\nAll {len(PROBES)} schemas produced valid structured output.")
    return 0


@command("llm-smoke", help="Probe one LLM provider against all 5 production schemas.")
@click.option(
    "--provider",
    type=click.Choice([MINIMAX, OPENAI]),
    default=MINIMAX,
    show_default=True,
    help="Which provider to probe. No fallback is used.",
)
@click.option(
    "--runs",
    type=int,
    default=3,
    show_default=True,
    help="Attempts per schema. >1 also surfaces latency spread and flakiness.",
)
def llm_smoke(provider: str, runs: int) -> None:
    if provider == MINIMAX and not settings.MINIMAX_API_KEY:
        error("MINIMAX_API_KEY is empty — set it in backend/.env before probing.")
        raise SystemExit(1)
    if provider == OPENAI and not settings.OPENAI_API_KEY:
        error("OPENAI_API_KEY is empty — set it in backend/.env before probing.")
        raise SystemExit(1)

    exit_code = asyncio.run(_run(provider, runs))
    if exit_code:
        raise SystemExit(exit_code)
