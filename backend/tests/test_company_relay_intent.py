"""Golden corpus + unit tests for the widened company-relay intent parser.

Regression: job ``fe12af88`` (326 Huntington Ln, Elmhurst, Always 24/7).
The operator re-pasted the job with "Tech was arriving but cx stop
answering check if can contact", then a minute later wrote "Cx answered
now, said already got help". The job stayed ``pending`` and later tripped a
false-positive ``closing_missing``.

Two independent reasons, both covered here:

1. ``reject_detector.is_cancel_signal`` recognised the cancel *wording* of
   the second message (``_looks_like_cancel_note`` → True) but returned
   False anyway, because it only fires on a re-paste of the full job block
   or a bare "DNS" token. See :func:`test_standalone_cancel_note_is_invisible_to_regex`.
2. ``CompanyRelayIntentCode`` was ``no_answer_follow_up | none``, so the
   LLM had no way to say "canceled".

The corpus below is drawn from real outbound OpenPhone traffic (deduped,
lightly trimmed) so the pre-filter and the prompt are exercised against the
vocabulary operators actually use — "60449 ok", "cx na", "Still prog",
"60108 saying DNS" — rather than invented tidy sentences.

The LLM-backed test is opt-in (``-m llm``): it makes real model calls, so
it is not part of the default run. Everything else is deterministic.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import InvalidTransitionError
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services import company_relay_parser, reject_detector
from app.services.company_relay_parser import _INTENT_TO_STATUS, should_parse
from app.services.lifecycle import LifecycleStatus, _validate_transition


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# The two messages from the motivating job, verbatim.
HUNTINGTON_JOB_BODY = """Co: Always 24/7
PDL: 7BGYE
Ph: 7702868093
Addr: 326 Huntington Ln , Elmhurst, IL, 60126
Desc: House Lock Out
Occu: Locksmith

Notes:"""

HUNTINGTON_UPDATE_1 = (
    HUNTINGTON_JOB_BODY + "\n\n\nTech was arriving but cx stop answering check if can contact"
)
HUNTINGTON_UPDATE_2 = "Cx answered now, said already got help"


# ---------------------------------------------------------------------------
# The regex gap that let the cancellation through
# ---------------------------------------------------------------------------


def test_standalone_cancel_note_is_invisible_to_regex() -> None:
    """The wording is recognised, yet is_cancel_signal still says no.

    This is the defect in one assertion: ``_looks_like_cancel_note`` sees a
    cancellation, but because the message is a standalone sentence rather
    than a re-paste of the job block, ``is_cancel_signal`` discards it.
    Hence the LLM intent set had to carry ``canceled``.
    """
    assert reject_detector._looks_like_cancel_note(HUNTINGTON_UPDATE_2) is True
    assert reject_detector.is_cancel_signal(HUNTINGTON_UPDATE_2, HUNTINGTON_JOB_BODY) is False
    assert reject_detector.is_reject_signal(HUNTINGTON_UPDATE_2, HUNTINGTON_JOB_BODY) is False


def test_update_1_is_correctly_not_a_cancel() -> None:
    """ "...check if can contact" is an open question, not a cancellation.

    The data-question veto is right here — at that moment the job was still
    being worked. It belongs in ``needs_follow_up``, which is what the
    relay parser now produces.
    """
    assert reject_detector.is_cancel_signal(HUNTINGTON_UPDATE_1, HUNTINGTON_JOB_BODY) is False
    assert reject_detector._looks_like_data_question(HUNTINGTON_UPDATE_1) is True


# ---------------------------------------------------------------------------
# Pre-filter — the old gate dropped these before the model ever saw them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["ok", "OK!", "ok.", "k", "ty", "thanks", "Thank you", "got it", "yes", "  ok  ", "np"],
)
def test_trivial_acks_skip_the_model(body: str) -> None:
    assert should_parse(body) is False


@pytest.mark.parametrize(
    "body",
    [
        HUNTINGTON_UPDATE_2,
        "Cx cancelling tech is 5 min away",
        "60108 saying DNS",
        "cx na",
        "Still prog",
        "On way",
        "11-11:30  appt",
        "60015 appt confirmed",
        "60449   job done tech busy and will send closing later, but its closed",
        "46312  we left vm too",
        "Good morning",
        "Ok ok",
    ],
)
def test_substantive_messages_reach_the_model(body: str) -> None:
    """Anything that is not a bare ack gets a call.

    Deliberately includes "Good morning" and "Ok ok": a wasted call that
    returns ``none`` is cheap, a dropped cancellation is not. The old
    contact-attempt keyword gate rejected 8 of these 12.
    """
    assert should_parse(body) is True


def test_old_keyword_gate_would_have_dropped_the_cancellation() -> None:
    """Documents why the pre-filter was replaced rather than extended."""
    assert (
        reject_detector.mentions_customer_contact_attempt("Cx cancelling tech is 5 min away")
        is False
    )
    assert should_parse("Cx cancelling tech is 5 min away") is True


# ---------------------------------------------------------------------------
# Intent → status mapping
# ---------------------------------------------------------------------------


def test_every_actionable_intent_maps_to_a_status() -> None:
    """``CompanyRelayIntentCode`` and the mapping must not drift apart."""
    from typing import get_args

    from app.schemas.dispatch_job import CompanyRelayIntentCode

    codes = set(get_args(CompanyRelayIntentCode))
    assert codes - {"none"} == set(_INTENT_TO_STATUS)
    assert "none" not in _INTENT_TO_STATUS


def test_cancel_intent_maps_to_canceled() -> None:
    assert _INTENT_TO_STATUS["canceled"] is LifecycleStatus.CANCELED
    assert _INTENT_TO_STATUS["no_answer_follow_up"] is LifecycleStatus.NEEDS_FOLLOW_UP


# ---------------------------------------------------------------------------
# Precedence guard — a relayed remark may not undo a settled job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("settled", ["closed", "completed", "rejected", "canceled"])
def test_relay_cannot_overwrite_a_settled_job(settled: str) -> None:
    with pytest.raises(InvalidTransitionError):
        _validate_transition(
            from_status=settled,
            to_status=LifecycleStatus.CANCELED,
            source=LifecycleEventSource.OPERATOR_RELAY,
        )


@pytest.mark.parametrize(
    "open_status",
    ["pending", "dispatched", "accepted", "in_progress", "appt_set", "needs_follow_up"],
)
def test_relay_may_write_over_an_open_job(open_status: str) -> None:
    _validate_transition(
        from_status=open_status,
        to_status=LifecycleStatus.CANCELED,
        source=LifecycleEventSource.OPERATOR_RELAY,
    )


def test_guard_is_scoped_to_relay_only() -> None:
    """The closing pipeline must stay able to close a completed job."""
    _validate_transition(
        from_status="completed",
        to_status=LifecycleStatus.CLOSED,
        source=LifecycleEventSource.CLOSING_CHAT,
    )


# ---------------------------------------------------------------------------
# Attribution — sticky reference, and refusal to guess
# ---------------------------------------------------------------------------


def _op_msg(body: str, *, ts: datetime, to: str = "+14704714943") -> SimpleNamespace:
    return SimpleNamespace(
        content=body,
        to_numbers=[to],
        created_at=ts,
        openphone_id="OP_TEST",
        direction="outgoing",
    )


@pytest.mark.anyio
async def test_keyless_update_inherits_reference_from_the_thread() -> None:
    """The exact Huntington sequence: update 2 names no job, update 1 does."""
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), lifecycle_status="pending")

    async def _find(db, *, counterparty, before, reference, **kw):
        # Only the re-pasted body carries keys; the bare follow-up does not.
        return (job, HUNTINGTON_JOB_BODY) if reference.pdl == "7BGYE" else None

    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(side_effect=_find),
        ),
        patch(
            "app.repositories.openphone.list_recent_outbound_bodies",
            new=AsyncMock(return_value=[HUNTINGTON_UPDATE_1]),
        ),
    ):
        resolved = await company_relay_parser._resolve_job(
            AsyncMock(),
            counterparty="+14704714943",
            body=HUNTINGTON_UPDATE_2,
            reply_at=now,
        )

    assert resolved is not None
    matched_job, matched_by = resolved
    assert matched_job is job
    assert matched_by == "sticky_reference"


@pytest.mark.anyio
async def test_refuses_to_guess_when_the_thread_has_no_reference() -> None:
    """No keys anywhere → no job. Never falls back to 'most recent open'."""
    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.repositories.openphone.list_recent_outbound_bodies",
            new=AsyncMock(return_value=["ok", "checking", "Good morning"]),
        ),
    ):
        resolved = await company_relay_parser._resolve_job(
            AsyncMock(),
            counterparty="+14704714943",
            body=HUNTINGTON_UPDATE_2,
            reply_at=datetime.now(UTC),
        )

    assert resolved is None


@pytest.mark.anyio
async def test_unattributed_actionable_update_raises_an_alert() -> None:
    """Actionable but unplaceable → one alert, no transition."""
    from app.db.models.alert import AlertKind

    intent = SimpleNamespace(
        intent="canceled", follow_up_at=None, appt_iso=None, reason="solved", notes=None
    )
    create_alert = AsyncMock()

    with (
        patch.object(company_relay_parser, "_extract_intent", new=AsyncMock(return_value=intent)),
        patch.object(company_relay_parser, "_resolve_job", new=AsyncMock(return_value=None)),
        patch("app.repositories.alert.create_or_get_open", new=create_alert),
    ):
        applied = await company_relay_parser.maybe_apply_relay_update(
            AsyncMock(), _op_msg(HUNTINGTON_UPDATE_2, ts=datetime.now(UTC))
        )

    assert applied is False
    assert create_alert.await_count == 1
    assert create_alert.await_args.kwargs["kind"] == AlertKind.UNATTRIBUTED_UPDATE.value


@pytest.mark.anyio
async def test_plain_ack_never_alerts_and_never_calls_the_model() -> None:
    extract = AsyncMock()
    create_alert = AsyncMock()

    with (
        patch.object(company_relay_parser, "_extract_intent", new=extract),
        patch("app.repositories.alert.create_or_get_open", new=create_alert),
    ):
        applied = await company_relay_parser.maybe_apply_relay_update(
            AsyncMock(), _op_msg("ok", ts=datetime.now(UTC))
        )

    assert applied is False
    assert extract.await_count == 0
    assert create_alert.await_count == 0


@pytest.mark.anyio
async def test_intent_none_is_silent() -> None:
    """A real message with no outcome must not create an alert."""
    intent = SimpleNamespace(
        intent="none", follow_up_at=None, appt_iso=None, reason=None, notes=None
    )
    create_alert = AsyncMock()
    resolve = AsyncMock()

    with (
        patch.object(company_relay_parser, "_extract_intent", new=AsyncMock(return_value=intent)),
        patch.object(company_relay_parser, "_resolve_job", new=resolve),
        patch("app.repositories.alert.create_or_get_open", new=create_alert),
    ):
        applied = await company_relay_parser.maybe_apply_relay_update(
            AsyncMock(), _op_msg("Good morning guys, have a good day", ts=datetime.now(UTC))
        )

    assert applied is False
    assert create_alert.await_count == 0
    assert resolve.await_count == 0  # short-circuits before attribution


@pytest.mark.anyio
async def test_cancel_transition_carries_a_note_and_relay_source() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), lifecycle_status="pending")
    intent = SimpleNamespace(
        intent="canceled",
        follow_up_at=None,
        appt_iso=None,
        reason="solved",
        notes="customer already got help",
    )
    transition = AsyncMock(return_value=uuid4())

    with (
        patch.object(company_relay_parser, "_extract_intent", new=AsyncMock(return_value=intent)),
        patch.object(
            company_relay_parser,
            "_resolve_job",
            new=AsyncMock(return_value=(job, "sticky_reference")),
        ),
        patch("app.services.lifecycle.LifecycleService.transition", new=transition),
    ):
        applied = await company_relay_parser.maybe_apply_relay_update(
            AsyncMock(), _op_msg(HUNTINGTON_UPDATE_2, ts=now)
        )

    assert applied is True
    kwargs = transition.await_args.kwargs
    assert kwargs["to_status"] is LifecycleStatus.CANCELED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_RELAY
    assert kwargs["at"] == now
    assert kwargs["payload"]["reason"] == "solved"
    assert kwargs["payload"]["note"]
    assert kwargs["payload"]["matched_by"] == "sticky_reference"


@pytest.mark.anyio
async def test_sticky_window_is_bounded() -> None:
    """The thread lookup asks for a bounded window, not all history."""
    now = datetime.now(UTC)
    list_bodies = AsyncMock(return_value=[])

    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(return_value=None),
        ),
        patch("app.repositories.openphone.list_recent_outbound_bodies", new=list_bodies),
    ):
        await company_relay_parser._resolve_job(
            AsyncMock(), counterparty="+1470", body="cx na", reply_at=now
        )

    since = list_bodies.await_args.kwargs["since"]
    expected = now - timedelta(minutes=company_relay_parser.STICKY_REFERENCE_WINDOW_MINUTES)
    assert abs((since - expected).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Golden corpus — real outbound traffic, hand-labeled. Opt-in: makes real
# model calls. Run with:  pytest tests/test_company_relay_intent.py -m llm
# ---------------------------------------------------------------------------

# (body, expected_intent). Drawn from production outbound OpenPhone
# messages. "60449"-style prefixes are ZIP codes the operators type as a
# shorthand job tag; they are part of the real vocabulary and left intact.
GOLDEN_CORPUS: list[tuple[str, str]] = [
    # --- the motivating pair -------------------------------------------
    (HUNTINGTON_UPDATE_2, "canceled"),
    (HUNTINGTON_UPDATE_1, "no_answer_follow_up"),
    # --- cancellations --------------------------------------------------
    ("Cx cancelling tech is 5 min away", "canceled"),
    ("60108 saying DNS", "canceled"),
    ("cx canceled the appt", "canceled"),
    ("customer found someone else already", "canceled"),
    ("cx no longer needs it", "canceled"),
    (
        "60143 wants price to install $225 it's difficult job. Someone doing cheaper cx will go with them",
        "canceled",
    ),
    ("cx said he already fixed it himself", "canceled"),
    ("no longer need, cx got help", "canceled"),
    # --- no answer / still chasing --------------------------------------
    ("cx na", "no_answer_follow_up"),
    ("Na did not call back lef vm", "no_answer_follow_up"),
    ("46312  we left vm too", "no_answer_follow_up"),
    ("still no answer", "no_answer_follow_up"),
    ("tried again no pickup", "no_answer_follow_up"),
    ("cx not picking up, will try again", "no_answer_follow_up"),
    ("60453 na pls check if can reach cx", "no_answer_follow_up"),
    ("60172 CB", "no_answer_follow_up"),
    ("left vm twice no response yet", "no_answer_follow_up"),
    # --- in progress -----------------------------------------------------
    ("On way", "in_progress"),
    ("Still prog", "in_progress"),
    ("in progress", "in_progress"),
    ("10-15 mins", "in_progress"),
    ("tech otw", "in_progress"),
    ("tech is there now", "in_progress"),
    # --- appointments -----------------------------------------------------
    ("11-11:30  appt", "appt_set"),
    ("60015 appt confirmed", "appt_set"),
    ("cx wants tomorrow 3pm", "appt_set"),
    ("scheduled for 2pm today", "appt_set"),
    # --- completed --------------------------------------------------------
    ("60449   job done tech busy and will send closing later, but its closed", "completed"),
    ("done, paid 240 cash", "completed"),
    ("job completed", "completed"),
    # --- none: acks, chatter, availability --------------------------------
    ("Good morning", "none"),
    ("Good morning guys, have a good day", "none"),
    ("We are available for jobs, ty", "none"),
    ("We are available", "none"),
    ("Ok ok", "none"),
    ("ok ty", "none"),
    ("checking", "none"),
    ("ch ch", "none"),
    ("60107 ok", "none"),
    ("60426  ty", "none"),
    # --- none: questions and requests to the broker -----------------------
    ("Any update/closing missing?", "none"),
    ("60426 updt pls", "none"),
    ("60201 reached?", "none"),
    ("60433 k?", "none"),
    ("60438 pls check if still need", "none"),
    ("60449  ch if can do", "none"),
    ("plz update ?", "none"),
    # --- none: capacity / logistics, not job outcomes ---------------------
    ("60192 ch still looking for someone", "none"),
    ("60448 atm have nobody for it, it's too far, I'm waiting for 7 to get more techs", "none"),
    ("60106 I'm waiting for tech closing", "none"),
    ("will send once we have", "none"),
    ("will send soon", "none"),
    (
        "Hello! this is locksmith services, our technician is contacting you. Please let us know when you'll be available por services",
        "none",
    ),
]


@pytest.mark.llm
@pytest.mark.anyio
@pytest.mark.parametrize("body,expected", GOLDEN_CORPUS)
async def test_golden_corpus_intent(body: str, expected: str) -> None:
    """Each labeled message must classify to its expected intent.

    Real model call. Skipped unless ``-m llm`` is passed.
    """
    from app.db.session import get_db_context

    async with get_db_context() as db:
        intent = await company_relay_parser._extract_intent(db, body)

    assert intent.intent == expected, f"{body!r} → {intent.intent} (expected {expected})"


@pytest.mark.llm
@pytest.mark.anyio
async def test_corpus_pre_filter_agreement() -> None:
    """No corpus entry labeled actionable may be dropped by the pre-filter."""
    for body, expected in GOLDEN_CORPUS:
        if expected != "none":
            assert should_parse(body) is True, f"pre-filter would drop {body!r}"


# ---------------------------------------------------------------------------
# ``rejected`` intent — operator declines the job in the broker thread
# ---------------------------------------------------------------------------
#
# Regression: job ``0964f020`` (Always 24/7, PDL HTE27, Melrose Park, 2023
# Ford Transit). "only dealer" is locksmith shorthand for "this needs a
# dealer-supplied key, we can't do it". Before this change the relay intent
# set had no code for an operator-side decline at all, so even a perfect
# model read had to answer ``none`` and the job stayed ``pending``.

ONLY_DEALER_UPDATE = "only dealer"


def test_rejected_intent_maps_to_rejected_status() -> None:
    """The intent set can express an operator-side decline."""
    assert _INTENT_TO_STATUS["rejected"] == LifecycleStatus.REJECTED


def test_rejected_is_a_valid_intent_code() -> None:
    from typing import get_args

    from app.schemas.dispatch_job import CompanyRelayIntentCode

    assert "rejected" in get_args(CompanyRelayIntentCode)


def test_only_dealer_survives_the_prefilter() -> None:
    """The cheap gate must not drop the update before the model sees it."""
    assert should_parse(ONLY_DEALER_UPDATE) is True


def test_relay_may_write_rejected_onto_a_pending_job() -> None:
    """``operator_relay`` is allowed to move a pending job to ``rejected``."""
    _validate_transition(
        from_status=LifecycleStatus.PENDING.value,
        to_status=LifecycleStatus.REJECTED,
        source=LifecycleEventSource.OPERATOR_RELAY,
    )


def test_relay_may_not_reject_an_already_settled_job() -> None:
    """A late "only dealer" cannot undo a close — the relay guard still holds."""
    with pytest.raises(InvalidTransitionError):
        _validate_transition(
            from_status=LifecycleStatus.CLOSED.value,
            to_status=LifecycleStatus.REJECTED,
            source=LifecycleEventSource.OPERATOR_RELAY,
        )


@pytest.mark.anyio
async def test_relay_applies_rejected_transition() -> None:
    """A ``rejected`` intent transitions the resolved job through the gate."""
    db = AsyncMock()
    job = SimpleNamespace(id=uuid4(), lifecycle_status=LifecycleStatus.PENDING.value)
    reply_at = datetime.now(UTC)
    msg = SimpleNamespace(
        content=ONLY_DEALER_UPDATE,
        openphone_id="OP-HTE27",
        created_at=reply_at,
        to_numbers=["+14704714943"],
    )
    intent = SimpleNamespace(
        intent="rejected",
        follow_up_at=None,
        appt_iso=None,
        reason="dealer_only",
        notes=None,
    )

    with (
        patch.object(company_relay_parser, "_extract_intent", new=AsyncMock(return_value=intent)),
        patch.object(
            company_relay_parser,
            "_resolve_job",
            new=AsyncMock(return_value=(job, "sticky_reference")),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        applied = await company_relay_parser.maybe_apply_relay_update(db, msg)

    assert applied is True
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.REJECTED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_RELAY
    assert kwargs["payload"]["reason"] == "dealer_only"
