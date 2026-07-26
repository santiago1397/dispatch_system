"""Customer-never-answered cancellation + job-reference targeting (Quo).

Two production problems are covered here, both surfaced by the same job —
"Co: Always 24/7 / PDL: PY3YA" / 6946 N Overhill Ave, Chicago IL, which sat
at ``pending`` for six days after the operator had already reported it dead:

1. **Cancel vocabulary.** The operator re-pasted the job to the broker with
   "cx not answering to me or to new technician I left vm". That note
   matched ``_CONTACT_ATTEMPT_NOTE_RE`` (on "left vm"), which vetoed the
   reject path, while "not answering" appeared in no cancel pattern at all —
   so nothing fired. The distinction that now decides it is whether the note
   reports an OUTCOME ("not answering", "never answered") or merely an
   ACTION ("left vm").

2. **Job targeting.** Both OpenPhone candidate lookups took "the most recent
   open job from this counterparty". One broker (AMS) carries ~176
   concurrent open jobs, so that is close to a coin flip — updates landed on
   the wrong job. Replies that re-paste the job now resolve it by the
   identity they name (PDL / customer phone / address).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services import reject_detector
from app.services.job_reference import extract_job_reference
from app.services.lifecycle import LifecycleStatus
from app.services.openphone import OpenPhoneService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


JOB_BODY = (
    "Co: Always 24/7\n"
    "PDL: PY3YA\n"
    "Ph: 7739992940 \n"
    "Addr: 6946 N Overhill Ave , Chicago, IL, 60631\n"
    "Desc: Bedroom Lockout\n"
    "Occu: Locksmith\n\n"
    "Notes:"
)

CANCEL_REPLY = JOB_BODY + "\n\n\ncx not answering to me or to new technician I left vm"


def _op_msg(body: str, *, ts: datetime, to: str = "+14704714943") -> SimpleNamespace:
    return SimpleNamespace(
        content=body,
        to_numbers=[to],
        created_at=ts,
        openphone_id="OP_ABC",
        direction="outgoing",
    )


# ---------------------------------------------------------------------------
# Cancel vocabulary
# ---------------------------------------------------------------------------


def test_overhill_repaste_is_cancel_not_reject() -> None:
    assert reject_detector.is_cancel_signal(CANCEL_REPLY, JOB_BODY) is True
    # Must NOT read as a plain decline — the job was accepted and worked.
    assert reject_detector.is_reject_signal(CANCEL_REPLY, JOB_BODY) is False


@pytest.mark.parametrize(
    "note",
    [
        "cx not answering to me or to new technician I left vm",
        "Cx never answered to us or tech to set appt",
        "called and texted no answer",
        "customer did not answer the phone",
        "cx is not picking up",
        "no pick up",
        "cx not responding",
        "cx unresponsive",
        "cant reach the cx",
    ],
)
def test_settled_no_answer_note_is_cancel(note: str) -> None:
    assert reject_detector.is_cancel_signal(JOB_BODY + "\n" + note, JOB_BODY) is True


@pytest.mark.parametrize(
    "note",
    [
        "no answer yet",
        "not answering for now",
        "no answer so far",
        "cx not answering, still trying",
        "no answer, will keep trying",
        "no answer, trying again in 20",
    ],
)
def test_tentative_no_answer_note_is_not_cancel(note: str) -> None:
    # These stay non-terminal — the operator is still chasing the customer,
    # which is the ``needs_follow_up`` relay path, not a cancellation.
    assert reject_detector.is_cancel_signal(JOB_BODY + "\n" + note, JOB_BODY) is False


@pytest.mark.parametrize(
    "note",
    [
        # An open question means the operator is still working the job.
        "No answer, check please",
        "Comment: K?  Called and texted no answer",
        # This one is about an appointment that exists — canceling it would
        # be actively wrong.
        "Appt 9am pls check cx not answering try confirm appt",
    ],
)
def test_no_answer_note_asking_a_question_is_not_cancel(note: str) -> None:
    assert reject_detector.is_cancel_signal(JOB_BODY + "\n" + note, JOB_BODY) is False


def test_no_answer_mentioning_appt_without_asking_is_still_cancel() -> None:
    # "never answered ... to set appt" references an appointment but asks
    # nothing, and reports the appointment was never made.
    note = "please call cx to set appt  Cx never answered to us or tech to set appt"
    assert reject_detector.is_cancel_signal(JOB_BODY + "\n" + note, JOB_BODY) is True


def test_settled_physical_fact_ignores_tentative_marker() -> None:
    # A tentative marker cannot soften a settled fact: the customer already
    # has another vendor on site regardless of "will keep you posted".
    reply = JOB_BODY + "\nshe has someone on site already, will keep you posted"
    assert reject_detector.is_cancel_signal(reply, JOB_BODY) is True


def test_bare_contact_attempt_still_not_a_cancel() -> None:
    # Guards the existing "stvm left vm and text" regression: an ACTION with
    # no reported OUTCOME is progress, not a cancellation.
    reply = JOB_BODY + "\nstvm left vm and text"
    assert reject_detector.is_cancel_signal(reply, JOB_BODY) is False
    assert reject_detector.is_reject_signal(reply, JOB_BODY) is False


# ---------------------------------------------------------------------------
# Job-reference extraction
# ---------------------------------------------------------------------------


def test_extract_job_reference_from_repaste() -> None:
    ref = extract_job_reference(CANCEL_REPLY)
    assert ref.pdl == "PY3YA"
    assert (ref.customer_phone_e164 or "").endswith("7739992940")
    assert ref.street_number == "6946"
    assert "overhill" in (ref.street_name or "").lower()
    assert ref.has_address is True
    assert bool(ref) is True


def test_extract_job_reference_empty_for_bare_reply() -> None:
    ref = extract_job_reference("dns")
    assert ref.pdl is None
    assert ref.customer_phone_e164 is None
    assert ref.has_address is False
    assert bool(ref) is False


# ---------------------------------------------------------------------------
# Orchestration: targeting + window rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_is_exempt_from_two_message_window() -> None:
    # A cancellation is reported after a tech has been dispatched and gone to
    # the address — routinely many operator messages later. The reject
    # window must not suppress it.
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(hours=4))
    svc = OpenPhoneService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.openphone.openphone_repo.count_outbound_messages_to",
            new=AsyncMock(return_value=9),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await svc.maybe_reject_job(_op_msg(CANCEL_REPLY, ts=now))

    assert result is True
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.CANCELED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_CANCEL
    assert kwargs["payload"]["matched_by"] == "reference"


@pytest.mark.anyio
async def test_reject_still_honours_two_message_window() -> None:
    # The window still applies to a plain decline.
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(hours=4))
    svc = OpenPhoneService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.repositories.job.find_reject_candidate_openphone",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.openphone.openphone_repo.count_outbound_messages_to",
            new=AsyncMock(return_value=9),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await svc.maybe_reject_job(_op_msg("pass", ts=now))

    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_prefers_reference_match_over_recency() -> None:
    # The reply names PDL PY3YA; the recency lookup would have returned an
    # unrelated, newer job. The named job must win.
    now = datetime.now(UTC)
    named = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(hours=3))
    recent = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=1))
    svc = OpenPhoneService(db=AsyncMock())

    recency = AsyncMock(return_value=(recent, JOB_BODY))
    with (
        patch(
            "app.repositories.job.find_job_by_reference_openphone",
            new=AsyncMock(return_value=(named, JOB_BODY)),
        ),
        patch("app.repositories.job.find_reject_candidate_openphone", new=recency),
        patch(
            "app.services.openphone.openphone_repo.count_outbound_messages_to",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await svc.maybe_reject_job(_op_msg(CANCEL_REPLY, ts=now))

    assert result is True
    assert ls_cls.return_value.transition.await_args.kwargs["job"] is named
    recency.assert_not_awaited()


@pytest.mark.anyio
async def test_falls_back_to_recency_when_reply_names_no_job() -> None:
    # A bare "dns" carries no identity keys, so the recency lookup is the
    # only option and must still be used.
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=5))
    svc = OpenPhoneService(db=AsyncMock())

    reference = AsyncMock(return_value=None)
    with (
        patch("app.repositories.job.find_job_by_reference_openphone", new=reference),
        patch(
            "app.repositories.job.find_reject_candidate_openphone",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.openphone.openphone_repo.count_outbound_messages_to",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await svc.maybe_reject_job(_op_msg("dns", ts=now))

    assert result is True
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.CANCELED
    assert kwargs["payload"]["matched_by"] == "recency"
    # No identity keys in "dns", so the reference lookup is skipped entirely.
    reference.assert_not_awaited()
