"""Tests for the operator job-rejection feature.

Two layers:
- Pure signal detection (``reject_detector``): phrases, "<zip> pass",
  and re-paste-with-note, plus the non-reject negatives that must NOT
  trip it (full job messages, status replies).
- Orchestration (``WhatsappService._maybe_reject_job``): the
  two-operator-message window, no-candidate short-circuit, and the
  lifecycle transition to ``rejected``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services import reject_detector
from app.services.lifecycle import LifecycleStatus
from app.services.openphone import OpenPhoneService
from app.services.whatsapp import WhatsappService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# A realistic job body for the re-paste tests.
JOB_BODY = (
    "New job\nAddress: 1425 W Belmont Ave, Chicago, IL 60657\n"
    "Customer: John D 312-555-0182\nHouse lockout, needs ASAP"
)


# ---------------------------------------------------------------------------
# Phrase detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "pass",
        "Pass",
        "PASS.",
        "have it",
        "I have it",
        "i have it!",
        "we have it",
        "cant take",
        "can't take",
        "cannot take",
        "cant take it",
        "60657 pass",
        "pass 60657",
    ],
)
def test_reject_phrases_positive(body: str) -> None:
    assert reject_detector.is_reject_phrase(body) is True
    assert reject_detector.is_reject_signal(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   ",
        "on my way",
        "yes",
        "ok will do",
        "passenger side lock",  # contains "pass" as substring, not a token
        "customer wants to reschedule to pass by tomorrow at 3",  # long, has "pass"
        JOB_BODY,  # a full job message is never a reject phrase
    ],
)
def test_reject_phrases_negative(body: str) -> None:
    assert reject_detector.is_reject_phrase(body) is False


def test_zip_pass_requires_short_message() -> None:
    # A long message that merely contains a ZIP and the word "pass" must
    # not be read as a "<zip> pass" reject.
    long_body = "please pass this to the tech, address is 60657 near the corner store downtown"
    assert reject_detector.is_reject_phrase(long_body) is False


# ---------------------------------------------------------------------------
# Re-paste with note
# ---------------------------------------------------------------------------


def test_repaste_with_note_is_reject() -> None:
    reply = JOB_BODY + "\npass, too far for us today"
    assert reject_detector.is_repaste_with_note(reply, JOB_BODY) is True
    assert reject_detector.is_reject_signal(reply, JOB_BODY) is True


def test_bare_repaste_without_note_is_not_reject() -> None:
    # Copying the job verbatim with no added note is not a decline.
    assert reject_detector.is_repaste_with_note(JOB_BODY, JOB_BODY) is False


def test_unrelated_long_reply_is_not_repaste() -> None:
    reply = "Different job entirely at 900 N Michigan Ave, car lockout, customer waiting outside"
    assert reject_detector.is_repaste_with_note(reply, JOB_BODY) is False


def test_repaste_ignored_for_trivially_short_job_body() -> None:
    assert reject_detector.is_repaste_with_note("123 pass no", "123 Main") is False


def test_signal_without_job_body_only_checks_phrases() -> None:
    reply = JOB_BODY + "\ntoo far"
    # No job_body passed → re-paste path can't run, and this isn't a phrase.
    assert reject_detector.is_reject_signal(reply, None) is False


@pytest.mark.parametrize(
    "note",
    [
        "Comment: K?\n\nIt says the wrong number pls check",
        "K?",
        "is this the right address?",
        "pls confirm the phone number",
        "can you verify the address",
        "correct number?",
    ],
)
def test_repaste_with_data_question_is_not_reject(note: str) -> None:
    # A re-paste + a data-quality question ("wrong number, pls check") is
    # NOT a decline — the operator is flagging bad info, not passing on
    # the job. Regression test for a job wrongly marked `rejected` when
    # the customer simply hadn't answered yet.
    reply = JOB_BODY + "\n" + note
    assert reject_detector.is_repaste_with_note(reply, JOB_BODY) is False
    assert reject_detector.is_reject_signal(reply, JOB_BODY) is False


def test_repaste_with_genuine_decline_note_still_rejects() -> None:
    # Guard against the data-question veto swallowing real declines.
    reply = JOB_BODY + "\npass, too far for us today"
    assert reject_detector.is_repaste_with_note(reply, JOB_BODY) is True
    assert reject_detector.is_reject_signal(reply, JOB_BODY) is True


# ---------------------------------------------------------------------------
# Orchestration: WhatsappService._maybe_reject_job
# ---------------------------------------------------------------------------


def _msg(body: str, *, ts: datetime, chat_jid: str = "wa-local:acme") -> SimpleNamespace:
    return SimpleNamespace(
        body=body,
        chat_jid=chat_jid,
        timestamp=ts,
        wa_message_id="OP_MSG_1",
        is_from_me=True,
    )


@pytest.mark.anyio
async def test_maybe_reject_transitions_within_window() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=2))
    svc = WhatsappService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_reject_candidate",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.whatsapp.whatsapp_repo.count_operator_messages_between",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await svc._maybe_reject_job(_msg("pass", ts=now), batch_id="b1")

    assert result is True
    ls_cls.return_value.transition.assert_awaited_once()
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.REJECTED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_REJECT
    assert kwargs["job"] is job


@pytest.mark.anyio
async def test_maybe_reject_skips_outside_window() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=5))
    svc = WhatsappService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_reject_candidate",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.whatsapp.whatsapp_repo.count_operator_messages_between",
            new=AsyncMock(return_value=3),  # the 3rd operator message — too late
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await svc._maybe_reject_job(_msg("pass", ts=now), batch_id="b1")

    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_maybe_reject_no_candidate() -> None:
    svc = WhatsappService(db=AsyncMock())
    with (
        patch(
            "app.repositories.job.find_reject_candidate",
            new=AsyncMock(return_value=None),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await svc._maybe_reject_job(_msg("pass", ts=datetime.now(UTC)), batch_id="b1")
    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_maybe_reject_not_a_reject_message() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=1))
    svc = WhatsappService(db=AsyncMock())
    with (
        patch(
            "app.repositories.job.find_reject_candidate",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await svc._maybe_reject_job(_msg("on my way", ts=now), batch_id="b1")
    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


# ---------------------------------------------------------------------------
# Orchestration: OpenPhoneService.maybe_reject_job (Quo path)
# ---------------------------------------------------------------------------


def _op_msg(body: str, *, ts: datetime, to: str = "+13125550182") -> SimpleNamespace:
    return SimpleNamespace(
        content=body,
        to_numbers=[to],
        created_at=ts,
        openphone_id="OP_ABC",
        direction="outgoing",
    )


@pytest.mark.anyio
async def test_openphone_reject_transitions_within_window() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=2))
    svc = OpenPhoneService(db=AsyncMock())

    with (
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
        result = await svc.maybe_reject_job(_op_msg("pass", ts=now))

    assert result is True
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.REJECTED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_REJECT
    assert kwargs["job"] is job


@pytest.mark.anyio
async def test_openphone_reject_skips_outside_window() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=9))
    svc = OpenPhoneService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_reject_candidate_openphone",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch(
            "app.services.openphone.openphone_repo.count_outbound_messages_to",
            new=AsyncMock(return_value=3),  # 3rd outbound message — too late
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await svc.maybe_reject_job(_op_msg("pass", ts=now))

    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_openphone_reject_not_a_reject_message() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), first_message_at=now - timedelta(minutes=1))
    svc = OpenPhoneService(db=AsyncMock())

    with (
        patch(
            "app.repositories.job.find_reject_candidate_openphone",
            new=AsyncMock(return_value=(job, JOB_BODY)),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await svc.maybe_reject_job(_op_msg("running late, 20 min", ts=now))

    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


# ---------------------------------------------------------------------------
# Technician accept / reject phrase detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ["ok", "OK", "Ok!", "k", "kk", "yes", "yep", "got it", "on it", "copy", "sure"]
)
def test_tech_accept_positive(body: str) -> None:
    assert reject_detector.is_tech_accept(body) is True
    assert reject_detector.is_tech_reject(body) is False


@pytest.mark.parametrize(
    "body", ["pass", "No", "no", "nope", "nah", "cant", "can't", "cannot", "cant take", "skip"]
)
def test_tech_reject_positive(body: str) -> None:
    assert reject_detector.is_tech_reject(body) is True
    assert reject_detector.is_tech_accept(body) is False


@pytest.mark.parametrize(
    "body",
    [
        "",
        "on my way",
        "running late 20 min",
        "cant make it, customer not home",  # long → not exact 'cant' → LLM (canceled)
        "no problem ill be there in 10",  # not exact 'no'
    ],
)
def test_tech_decision_negative(body: str) -> None:
    assert reject_detector.is_tech_accept(body) is False
    assert reject_detector.is_tech_reject(body) is False


# ---------------------------------------------------------------------------
# Orchestration: tech accept/reject in the reply parser
# ---------------------------------------------------------------------------


def _wa_reply(body: str, *, quoted: str | None = None, ts: datetime | None = None):
    return SimpleNamespace(
        body=body,
        chat_jid="tech-chat@g.us",
        wa_message_id="wamid.reply",
        quoted_wa_message_id=quoted,
        timestamp=ts or datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_tech_accept_quote_transitions_to_accepted() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_whatsapp

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))

    with patch("app.services.lifecycle.LifecycleService") as ls_cls:
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        # A direct quote skips the window count entirely.
        result = await _apply_tech_decision_whatsapp(
            db, _wa_reply("ok", quoted="wamid.dispatch"), event, job
        )

    assert result == ("tech_whatsapp", "accepted")
    assert ls_cls.return_value.transition.await_args.kwargs["to_status"] == LifecycleStatus.ACCEPTED


@pytest.mark.anyio
async def test_tech_reject_within_window_transitions_to_pending() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_whatsapp

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))

    with (
        patch(
            "app.services.tech_reply_parser.whatsapp_repo.count_tech_messages_between",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await _apply_tech_decision_whatsapp(db, _wa_reply("pass"), event, job)

    assert result == ("tech_whatsapp", "tech_rejected")
    assert ls_cls.return_value.transition.await_args.kwargs["to_status"] == LifecycleStatus.PENDING


@pytest.mark.anyio
async def test_tech_decision_outside_window_falls_through_to_llm() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_whatsapp

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))

    with (
        patch(
            "app.services.tech_reply_parser.whatsapp_repo.count_tech_messages_between",
            new=AsyncMock(return_value=3),  # 3rd tech message — too late
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await _apply_tech_decision_whatsapp(db, _wa_reply("ok"), event, job)

    assert result is None
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_tech_non_phrase_reply_falls_through() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_whatsapp

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))
    result = await _apply_tech_decision_whatsapp(db, _wa_reply("on my way"), event, job)
    assert result is None


@pytest.mark.anyio
async def test_openphone_tech_accept_transitions_to_accepted() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_openphone

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))
    msg = SimpleNamespace(
        content="k", openphone_id="OP1", created_at=datetime.now(UTC), lifecycle_event_id=None
    )

    with (
        patch(
            "app.services.tech_reply_parser.openphone_repo.count_inbound_messages_from",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await _apply_tech_decision_openphone(db, msg, "+13125550182", event, job)

    assert result == ("tech_openphone", "accepted")
    assert ls_cls.return_value.transition.await_args.kwargs["to_status"] == LifecycleStatus.ACCEPTED


@pytest.mark.anyio
async def test_openphone_tech_reject_transitions_to_pending() -> None:
    from app.services.tech_reply_parser import _apply_tech_decision_openphone

    db = AsyncMock()
    job = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=1))
    msg = SimpleNamespace(
        content="pass", openphone_id="OP2", created_at=datetime.now(UTC), lifecycle_event_id=None
    )

    with (
        patch(
            "app.services.tech_reply_parser.openphone_repo.count_inbound_messages_from",
            new=AsyncMock(return_value=2),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await _apply_tech_decision_openphone(db, msg, "+13125550182", event, job)

    assert result == ("tech_openphone", "tech_rejected")
    assert ls_cls.return_value.transition.await_args.kwargs["to_status"] == LifecycleStatus.PENDING
