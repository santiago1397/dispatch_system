"""Unit tests for the closing-signal gate (``services/closing_signal.py``).

Mocks the job repo + LifecycleService so no Postgres is needed — the gate's
logic is: token detection, attribute to an existing Job by address+phone, and
transition non-terminal matches to ``completed`` while dropping re-pastes that
land on already-completed/terminal jobs.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.closing_signal import ClosingSignalService, detect_payment_tokens

# A body with a re-pasted job (address + phone) plus a settlement line.
CLOSING_BODY = "123 N Main St, Chicago, IL 60601\n312-555-0198\nPaid $200 cash"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestDetectPaymentTokens:
    def test_fires_on_real_closing_examples(self):
        for body in (
            "Paid 100$",
            "Paid $200",
            "Paid 325.5",
            "Parts 45",
            "149 cash",
            "Paid:$600.00 cash",
            "Total:$600.00 cash",
            "Tech parts:$36.60",
            "Close 240 cash",
            "4100$cc",
        ):
            assert detect_payment_tokens(body) is not None, body

    def test_does_not_fire_on_plain_job_message(self):
        for body in (
            "123 N Main St, Chicago, IL 60601  312-555-0198  no hot water",
            "Lockout at 45 W Oak Ave, customer 7735551234, needs rekey",
            "ok on my way",
            "check the back door 123 Main",  # 'check' verb is excluded
        ):
            assert detect_payment_tokens(body) is None, body

    def test_does_not_fire_on_repasted_job_with_unrelated_keyword(self):
        # Regression: a re-pasted dispatch block whose footer note is just
        # "Close" (no amount) must not fire on unrelated digits elsewhere in
        # the block (phone number, zip, street number).
        body = (
            "Co: Always 24/7\n"
            "PDL: CV8IC\n"
            "N: ann\n"
            "Ph: 6175153471 \n"
            "Addr: 177 Dewindt Rd , Winnetka, IL, 60093\n"
            "Desc: Bedroom Lockout\n"
            "Occu: Locksmith\n\n"
            "Notes:\n\n"
            "Close"
        )
        assert detect_payment_tokens(body) is None, body


class TestGate:
    @pytest.mark.anyio
    async def test_completes_non_terminal_match(self):
        db = AsyncMock()
        job = SimpleNamespace(id=uuid4(), lifecycle_status="dispatched")

        with (
            patch(
                "app.services.closing_signal.job_repo.find_open_by_address_phone",
                new=AsyncMock(return_value=job),
            ),
            patch("app.services.closing_signal.LifecycleService") as lifecycle_cls,
        ):
            lifecycle_cls.return_value.transition = AsyncMock(return_value=uuid4())
            handled = await ClosingSignalService(db).detect_and_complete(
                body=CLOSING_BODY,
                channel="whatsapp",
                source_meta={"wa_message_id": "X"},
            )

        assert handled is True
        transition = lifecycle_cls.return_value.transition
        transition.assert_awaited_once()
        assert transition.call_args.kwargs["to_status"].value == "completed"
        assert transition.call_args.kwargs["source"] == "closing_signal"

    @pytest.mark.anyio
    async def test_drops_repaste_on_already_completed_job(self):
        db = AsyncMock()
        job = SimpleNamespace(id=uuid4(), lifecycle_status="completed")

        with (
            patch(
                "app.services.closing_signal.job_repo.find_open_by_address_phone",
                new=AsyncMock(return_value=job),
            ),
            patch("app.services.closing_signal.LifecycleService") as lifecycle_cls,
        ):
            handled = await ClosingSignalService(db).detect_and_complete(
                body=CLOSING_BODY,
                channel="whatsapp",
                source_meta={},
            )

        # Handled (short-circuit) but NO second transition.
        assert handled is True
        lifecycle_cls.return_value.transition.assert_not_called()

    @pytest.mark.anyio
    async def test_falls_through_when_no_payment_tokens(self):
        db = AsyncMock()
        with patch(
            "app.services.closing_signal.job_repo.find_open_by_address_phone",
            new=AsyncMock(),
        ) as find:
            handled = await ClosingSignalService(db).detect_and_complete(
                body="123 N Main St, 312-555-0198, no hot water",
                channel="whatsapp",
                source_meta={},
            )
        assert handled is False
        find.assert_not_called()

    @pytest.mark.anyio
    async def test_falls_through_when_no_job_matches(self):
        db = AsyncMock()
        with patch(
            "app.services.closing_signal.job_repo.find_open_by_address_phone",
            new=AsyncMock(return_value=None),
        ):
            handled = await ClosingSignalService(db).detect_and_complete(
                body=CLOSING_BODY,
                channel="openphone",
                source_meta={},
            )
        assert handled is False
