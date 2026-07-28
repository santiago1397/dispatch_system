"""Operator declines a job: apology-prefixed phrases and the reject window.

Two real jobs stayed ``pending`` after the operator plainly declined them,
each blocked by a different gate:

- 22638 Lakeshore Dr — "sorry cant do". ``is_reject_phrase`` matched
  "cant do" but not the apologetic form, so no signal was ever raised.
- Buffalo Grove / PDL XK2FU — "sorry we have no key, catn take". The
  phrase matched, but the operator had already sent "Lmc" and "k", so the
  decline was the third outbound and the two-message cutoff dropped it.

Bodies below are copied verbatim from prod, typos included.
"""
# ruff: noqa: RUF001 - curly quotes are the point: phone keyboards produce
# them, and failing to normalize them is one of the bugs under test.

from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services import reject_detector


class TestApologeticDeclines:
    """A leading apology must not hide the decline behind it."""

    # Every outbound message in prod opening with "sorry" that is a decline.
    REAL_DECLINES: ClassVar[list[str]] = [
        "sorry cant do",
        "Sorry can’t do",  # curly apostrophe, exactly as prod stores it
        "Sorry can't do",
        "Sorry pass",
        "Sorry pass have nobody",
        "Sorry pass that have nobody for it",
        "sorry we have no key, catn take",
    ]

    # Known gap, left alone deliberately. "have nobody" on its own is not a
    # decline: "60448 atm have nobody for it, it's too far, I'm waiting for
    # 7 to get more techs" is an operator still working the job, and the
    # relay-intent corpus labels that shape ``none``. A keyword rule broad
    # enough to catch this would contradict it, so these are left to the
    # LLM intent path rather than the phrase matcher.
    KNOWN_UNDETECTED: ClassVar[list[str]] = ["Sorry guys short of tech atm have nobody for it"]

    @pytest.mark.parametrize("body", KNOWN_UNDETECTED)
    def test_bare_no_capacity_notes_are_left_to_the_llm(self, body: str) -> None:
        assert reject_detector.is_reject_phrase(body) is False

    # ...and the ones that merely start the same way. A blanket
    # "sorry => decline" rule would wrongly reject both of these.
    REAL_NON_DECLINES: ClassVar[list[str]] = [
        "Sorry it's Ooa",
        "sorry the technician got stuck in traffic, he is <10 mins",
    ]

    @pytest.mark.parametrize("body", REAL_DECLINES)
    def test_apologetic_decline_is_recognised(self, body: str) -> None:
        assert reject_detector.is_reject_phrase(body) is True

    @pytest.mark.parametrize("body", REAL_NON_DECLINES)
    def test_apology_alone_is_not_a_decline(self, body: str) -> None:
        assert reject_detector.is_reject_phrase(body) is False

    @pytest.mark.parametrize(
        "body",
        ["sry cant do", "srry pass", "sorry we cant take", "sorry but cant do"],
    )
    def test_spelling_and_filler_variants(self, body: str) -> None:
        assert reject_detector.is_reject_phrase(body) is True

    def test_unprefixed_phrases_still_match(self) -> None:
        """The original behaviour is untouched."""
        for body in ("cant do", "pass", "no can do", "cant do sorry"):
            assert reject_detector.is_reject_phrase(body) is True

    def test_a_bare_apology_is_not_a_decline(self) -> None:
        for body in ("sorry", "sorry!", "sorry about that"):
            assert reject_detector.is_reject_phrase(body) is False

    @pytest.mark.parametrize(
        "body",
        ["can’t do", "can’t take", "can’t take this", "pass — can’t do"],
    )
    def test_curly_punctuation_normalizes_like_straight(self, body: str) -> None:
        """Phone keyboards substitute curly quotes and dashes automatically,
        so these are the forms operators actually send."""
        assert reject_detector.is_reject_phrase(body) is True


def _job(first_message_at: datetime):
    job = MagicMock()
    job.id = uuid4()
    job.first_message_at = first_message_at
    job.lifecycle_status = "pending"
    return job


def _outbound_message(body: str, at: datetime, counterparty: str = "+18182755551"):
    msg = MagicMock()
    msg.content = body
    msg.created_at = at
    msg.to_numbers = [counterparty]
    msg.openphone_id = "OP123"
    return msg


class TestRejectWindow:
    """The two-message cutoff must not drop an unambiguous decline."""

    @staticmethod
    async def _run(*, outbound_count: int, competing: int) -> str | None:
        """Drive maybe_reject_job and report the status it transitioned to."""
        from app.services.openphone import OpenPhoneService

        arrived = datetime(2026, 7, 23, 21, 59, 35, tzinfo=UTC)
        reply_at = arrived + timedelta(minutes=22)
        job = _job(arrived)
        message = _outbound_message("sorry we have no key, catn take", reply_at)

        applied: dict = {}

        async def _transition(**kwargs):
            applied["to_status"] = kwargs["to_status"]
            applied["source"] = kwargs["source"]

        with (
            patch(
                "app.repositories.job.find_job_by_reference_openphone",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.repositories.job.find_reject_candidate_openphone",
                new=AsyncMock(return_value=(job, "Ref: Usafe Locksmith\nPDL: XK2FU")),
            ),
            patch(
                "app.repositories.job.count_newer_jobs_openphone",
                new=AsyncMock(return_value=competing),
            ),
            patch(
                "app.repositories.openphone.count_outbound_messages_to",
                new=AsyncMock(return_value=outbound_count),
            ),
            patch("app.services.lifecycle.LifecycleService") as mock_lifecycle,
        ):
            mock_lifecycle.return_value.transition = AsyncMock(side_effect=_transition)
            await OpenPhoneService(MagicMock()).maybe_reject_job(message)

        status = applied.get("to_status")
        return getattr(status, "value", status)

    @pytest.mark.anyio
    async def test_within_the_cutoff_still_rejects(self) -> None:
        assert await self._run(outbound_count=2, competing=0) == "rejected"

    @pytest.mark.anyio
    async def test_late_decline_applies_when_no_other_job_arrived(self) -> None:
        """The XK2FU case: third outbound after "Lmc" and "k", but the
        broker posted nothing else, so the decline is unambiguous."""
        assert await self._run(outbound_count=3, competing=0) == "rejected"

    @pytest.mark.anyio
    async def test_late_decline_is_dropped_when_another_job_arrived(self) -> None:
        """The reason the cutoff exists — a newer job makes the target
        genuinely ambiguous, so guessing is worse than doing nothing."""
        assert await self._run(outbound_count=3, competing=1) is None
