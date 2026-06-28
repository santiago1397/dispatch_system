"""Tests for service layer."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AlreadyExistsError, AuthenticationError, NotFoundError
from app.db.models.company import Company
from app.db.models.dispatch_job import ClassificationStatus
from app.db.models.job import Job
from app.db.models.openphone import IncomingMessage, MessageSource
from app.schemas.dispatch_job import JobExtraction
from app.schemas.user import UserCreate, UserUpdate
from app.services.address_normalizer import (
    NormalizedAddress,
    normalize_address,
    normalize_street_name,
)
from app.services.classification import (
    DEDUP_WINDOW_DAYS,
    PHONE_PATTERN,
    JobClassificationService,
)
from app.services.user import UserService


class MockUser:
    """Mock user for testing."""

    def __init__(
        self,
        id=None,
        email="test@example.com",
        full_name="Test User",
        hashed_password="$2b$12$hashedpassword",
        is_active=True,
        is_superuser=False,
    ):
        self.id = id or uuid4()
        self.email = email
        self.full_name = full_name
        self.hashed_password = hashed_password
        self.is_active = is_active
        self.is_superuser = is_superuser


class TestUserServicePostgresql:
    """Tests for UserService with PostgreSQL."""

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        """Create mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db: AsyncMock) -> UserService:
        """Create UserService instance with mock db."""
        return UserService(mock_db)

    @pytest.fixture
    def mock_user(self) -> MockUser:
        """Create a mock user."""
        return MockUser()

    @pytest.mark.anyio
    async def test_get_by_id_success(self, user_service: UserService, mock_user: MockUser):
        """Test getting user by ID successfully."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=mock_user)

            result = await user_service.get_by_id(mock_user.id)

            assert result == mock_user
            mock_repo.get_by_id.assert_called_once()

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self, user_service: UserService):
        """Test getting non-existent user raises NotFoundError."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await user_service.get_by_id(uuid4())

    @pytest.mark.anyio
    async def test_get_by_email(self, user_service: UserService, mock_user: MockUser):
        """Test getting user by email."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=mock_user)

            result = await user_service.get_by_email("test@example.com")

            assert result == mock_user

    @pytest.mark.anyio
    async def test_get_multi(self, user_service: UserService, mock_user: MockUser):
        """Test getting multiple users."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_multi = AsyncMock(return_value=[mock_user])

            result = await user_service.get_multi(skip=0, limit=10)

            assert len(result) == 1
            assert result[0] == mock_user

    @pytest.mark.anyio
    async def test_register_success(self, user_service: UserService, mock_user: MockUser):
        """Test registering a new user."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=None)
            mock_repo.create = AsyncMock(return_value=mock_user)

            user_in = UserCreate(
                email="new@example.com",
                password="password123",
                full_name="New User",
            )
            result = await user_service.register(user_in)

            assert result == mock_user
            mock_repo.create.assert_called_once()

    @pytest.mark.anyio
    async def test_register_duplicate_email(self, user_service: UserService, mock_user: MockUser):
        """Test registering with existing email raises AlreadyExistsError."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=mock_user)

            user_in = UserCreate(
                email="existing@example.com",
                password="password123",
                full_name="Test",
            )

            with pytest.raises(AlreadyExistsError):
                await user_service.register(user_in)

    @pytest.mark.anyio
    async def test_authenticate_success(self, user_service: UserService, mock_user: MockUser):
        """Test successful authentication."""
        with (
            patch("app.services.user.user_repo") as mock_repo,
            patch("app.services.user.verify_password", return_value=True),
        ):
            mock_repo.get_by_email = AsyncMock(return_value=mock_user)

            result = await user_service.authenticate("test@example.com", "password123")

            assert result == mock_user

    @pytest.mark.anyio
    async def test_authenticate_invalid_password(
        self, user_service: UserService, mock_user: MockUser
    ):
        """Test authentication with wrong password."""
        with (
            patch("app.services.user.user_repo") as mock_repo,
            patch("app.services.user.verify_password", return_value=False),
        ):
            mock_repo.get_by_email = AsyncMock(return_value=mock_user)

            with pytest.raises(AuthenticationError):
                await user_service.authenticate("test@example.com", "wrongpassword")

    @pytest.mark.anyio
    async def test_authenticate_user_not_found(self, user_service: UserService):
        """Test authentication with non-existent user."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=None)

            with pytest.raises(AuthenticationError):
                await user_service.authenticate("unknown@example.com", "password")

    @pytest.mark.anyio
    async def test_authenticate_inactive_user(self, user_service: UserService):
        """Test authentication with inactive user."""
        inactive_user = MockUser(is_active=False)
        with (
            patch("app.services.user.user_repo") as mock_repo,
            patch("app.services.user.verify_password", return_value=True),
        ):
            mock_repo.get_by_email = AsyncMock(return_value=inactive_user)

            with pytest.raises(AuthenticationError):
                await user_service.authenticate("test@example.com", "password")

    @pytest.mark.anyio
    async def test_update_success(self, user_service: UserService, mock_user: MockUser):
        """Test updating user."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=mock_user)
            mock_repo.update = AsyncMock(return_value=mock_user)

            user_update = UserUpdate(full_name="Updated Name")
            result = await user_service.update(mock_user.id, user_update)

            assert result == mock_user

    @pytest.mark.anyio
    async def test_update_with_password(self, user_service: UserService, mock_user: MockUser):
        """Test updating user with password change."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=mock_user)
            mock_repo.update = AsyncMock(return_value=mock_user)

            user_update = UserUpdate(password="newpassword123")
            result = await user_service.update(mock_user.id, user_update)

            assert result == mock_user
            # Verify hashed_password was passed to update
            call_args = mock_repo.update.call_args
            assert "hashed_password" in call_args[1]["update_data"]

    @pytest.mark.anyio
    async def test_delete_success(self, user_service: UserService, mock_user: MockUser):
        """Test deleting user."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.delete = AsyncMock(return_value=mock_user)

            result = await user_service.delete(mock_user.id)

            assert result == mock_user

    @pytest.mark.anyio
    async def test_delete_not_found(self, user_service: UserService):
        """Test deleting non-existent user."""
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.delete = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await user_service.delete(uuid4())


# =============================================================================
# JobClassificationService — job gate, address normalization, and dedup pipeline
# =============================================================================


def _make_company(
    *,
    company_id=None,
    name="Acme Locksmith",
    display_name="Acme Locksmith",
    phone_numbers=None,
    identification_patterns=None,
    is_active=True,
) -> Company:
    """Construct an in-memory Company with the given fields."""
    c = Company(
        id=company_id or uuid4(),
        name=name,
        display_name=display_name,
        phone_numbers=phone_numbers or [],
        identification_patterns=identification_patterns or [],
        is_active=is_active,
    )
    return c


def _make_message(
    *,
    content: str,
    from_number: str | None = "+17735551212",
    source: str = MessageSource.OPENPHONE.value,
) -> IncomingMessage:
    """Construct an in-memory IncomingMessage with the given fields."""
    return IncomingMessage(
        id=uuid4(),
        source=source,
        openphone_id=None,
        direction="incoming",
        from_number=from_number,
        to_numbers=[],
        content=content,
        status=None,
        event_type="message.received",
        phone_number_id=None,
        raw_payload={},
    )


def _mock_extraction_llm(monkeypatch, *, address: str, job_type: str = "House Lockout") -> None:
    """Patch ChatOpenAI inside the classification module to return canned
    field-extraction output. Used by tests that go through the regex
    company path (so the company-classify LLM is never called)."""

    structured = MagicMock()
    structured.ainvoke = AsyncMock(
        return_value=JobExtraction(
            address=address,
            job_type=job_type,
            total="$250",
            customer_name="Jane Customer",
        )
    )
    instance = MagicMock()
    instance.with_structured_output.return_value = structured
    mock_class = MagicMock(return_value=instance)
    monkeypatch.setattr("app.services.classification.ChatOpenAI", mock_class)


class TestPhoneAndAddressDetection:
    """The strict job gate — a message is a candidate iff it contains
    a phone number AND an address pattern."""

    @pytest.mark.parametrize(
        "phone",
        [
            "7735551212",
            "(773) 555-1212",
            "773-555-1212",
            "773 555 1212",
            "+1 773 555 1212",
            "+17735551212",
            "1-773-555-1212",
            "1 (773) 555-1212",
        ],
    )
    def test_is_job_message_accepts_us_phone_formats(self, phone: str):
        content = f"Lockout at 123 Main St, Chicago, IL 60601. Call {phone}."
        assert PHONE_PATTERN.search(content), f"phone regex missed {phone!r}"
        assert JobClassificationService._is_job_message(content) is True

    def test_is_job_message_rejects_no_phone(self):
        content = "Lockout at 123 Main St, Chicago, IL 60601."
        assert JobClassificationService._is_job_message(content) is False

    def test_is_job_message_rejects_no_address(self):
        content = "Call 773-555-1212 to confirm the job."
        assert JobClassificationService._is_job_message(content) is False

    def test_is_job_message_rejects_empty(self):
        assert JobClassificationService._is_job_message("") is False


class TestAddressNormalizer:
    """The component-match dedup depends on accurate address parsing."""

    def test_normalize_address_full(self):
        result = normalize_address("123 N Main St, Chicago, IL 60601")
        assert result.street_number == "123"
        assert result.street_name == "north main street"
        assert result.city == "chicago"
        assert result.state == "IL"
        assert result.zip_code == "60601"

    def test_normalize_address_no_zip(self):
        result = normalize_address("123 Main St, Chicago, IL")
        assert result.street_number == "123"
        assert result.street_name == "main street"
        assert result.city == "chicago"
        assert result.state == "IL"
        assert result.zip_code is None

    def test_normalize_address_expanded_directional_and_suffix(self):
        # The dedup depends on "N Main St" and "North Main Street" collapsing
        # to the same key.
        a = normalize_address("123 N Main St, Chicago, IL 60601")
        b = normalize_address("123 North Main Street, Chicago, IL 60601")
        assert a.street_name == b.street_name == "north main street"

    def test_normalize_address_no_leading_number(self):
        # No street number — disqualified from dedup (street_number is None).
        result = normalize_address("Main Street, Chicago, IL 60601")
        assert result.street_number is None
        assert result.street_name is None

    def test_normalize_address_empty(self):
        result = normalize_address("")
        assert result == NormalizedAddress(
            raw="", street_number=None, street_name=None, city=None, state=None, zip_code=None
        )

    def test_normalize_street_name_preserves_stem(self):
        assert normalize_street_name("Maple") == "maple"
        assert normalize_street_name("Maple Ave") == "maple avenue"
        assert normalize_street_name("S Maple Ave") == "south maple avenue"


class TestJobClassificationDedup:
    """End-to-end dedup behavior — exercises the same company / cross-company
    / new-job / outside-window outcomes."""

    @pytest.mark.anyio
    async def test_classify_message_dedup_same_company(self, monkeypatch, mock_db_session):
        """A second message with the same address/job_type/company within
        the 14-day window should be LINKED to the existing Job (append-only)."""
        company = _make_company(
            identification_patterns=[{"patterns": [r"lockout", r"\d{3}-\d{3}-\d{4}"]}],
        )
        existing_job = MagicMock(spec=Job)
        existing_job.id = uuid4()
        existing_job.company_id = company.id
        existing_job.address_street_number = "123"
        existing_job.address_street_name = "north main street"
        existing_job.job_type = "House Lockout"
        existing_job.first_message_at = MagicMock()

        message = _make_message(
            content="Lockout at 123 N Main St, Chicago, IL 60601. 773-555-1212. $250."
        )
        pending_dj = MagicMock()
        pending_dj.id = uuid4()

        _mock_extraction_llm(monkeypatch, address="123 N Main St, Chicago, IL 60601")

        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.job_repo") as mock_job_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)
            mock_company_repo.get_all_active = AsyncMock(return_value=[company])
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_job_repo.find_dedup_candidate = AsyncMock(return_value=(existing_job, False))
            mock_job_repo.create_job = AsyncMock()

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        # find_dedup_candidate was consulted
        mock_job_repo.find_dedup_candidate.assert_awaited_once()
        # No new Job was created — we're appending to the existing one
        mock_job_repo.create_job.assert_not_called()
        # update_dispatch_job was called with LINKED status + dedup method + the
        # existing job_id.
        update_kwargs = mock_dj_repo.update_dispatch_job.await_args.kwargs
        assert update_kwargs["classification_status"] == ClassificationStatus.LINKED.value
        assert update_kwargs["classification_method"] == "dedup"
        assert update_kwargs["job_id"] == existing_job.id
        assert update_kwargs["company_id"] == company.id

    @pytest.mark.anyio
    async def test_classify_message_dedup_cross_company(self, monkeypatch, mock_db_session):
        """Same address/job_type but different company → a new Job is
        created with is_duplicate=True and duplicate_of pointing at the
        first-seen Job."""
        first_seen = MagicMock(spec=Job)
        first_seen.id = uuid4()
        first_seen.company_id = uuid4()  # different from current
        first_seen.address_street_number = "123"
        first_seen.address_street_name = "north main street"
        first_seen.job_type = "House Lockout"
        first_seen.first_message_at = MagicMock()

        current_company = _make_company(
            name="Beta Locksmith",
            identification_patterns=[{"patterns": [r"door", r"\d{3}-\d{3}-\d{4}"]}],
        )

        message = _make_message(
            content="Door lockout at 123 N Main St, Chicago, IL 60601. 773-555-1212."
        )
        pending_dj = MagicMock()
        pending_dj.id = uuid4()
        new_job = MagicMock(spec=Job)
        new_job.id = uuid4()

        _mock_extraction_llm(monkeypatch, address="123 N Main St, Chicago, IL 60601")

        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.job_repo") as mock_job_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)
            mock_company_repo.get_all_active = AsyncMock(return_value=[current_company])
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_job_repo.find_dedup_candidate = AsyncMock(return_value=(first_seen, True))
            mock_job_repo.create_job = AsyncMock(return_value=new_job)

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        # A new Job is created, flagged as a cross-company duplicate
        create_kwargs = mock_job_repo.create_job.await_args.kwargs
        assert create_kwargs["is_duplicate"] is True
        assert create_kwargs["duplicate_of"] == first_seen.id
        # The DispatchJob is CLASSIFIED (not LINKED) because it's a new job.
        update_kwargs = mock_dj_repo.update_dispatch_job.await_args.kwargs
        assert update_kwargs["classification_status"] == ClassificationStatus.CLASSIFIED.value
        assert update_kwargs["job_id"] == new_job.id

    @pytest.mark.anyio
    async def test_classify_message_new_job(self, monkeypatch, mock_db_session):
        """No dedup candidate → a fresh Job is created, DispatchJob is
        CLASSIFIED, is_duplicate=False."""
        company = _make_company(
            identification_patterns=[{"patterns": [r"rekey", r"\d{3}-\d{3}-\d{4}"]}],
        )
        message = _make_message(content="Rekey at 999 Oak Ave, Chicago, IL 60601. 773-555-1212.")
        pending_dj = MagicMock()
        pending_dj.id = uuid4()
        new_job = MagicMock(spec=Job)
        new_job.id = uuid4()

        _mock_extraction_llm(monkeypatch, address="999 Oak Ave, Chicago, IL 60601")

        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.job_repo") as mock_job_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)
            mock_company_repo.get_all_active = AsyncMock(return_value=[company])
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_job_repo.find_dedup_candidate = AsyncMock(return_value=(None, False))
            mock_job_repo.create_job = AsyncMock(return_value=new_job)

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        create_kwargs = mock_job_repo.create_job.await_args.kwargs
        assert create_kwargs["is_duplicate"] is False
        assert create_kwargs["duplicate_of"] is None
        update_kwargs = mock_dj_repo.update_dispatch_job.await_args.kwargs
        assert update_kwargs["classification_status"] == ClassificationStatus.CLASSIFIED.value

    @pytest.mark.anyio
    async def test_classify_message_outside_window(self, monkeypatch, mock_db_session):
        """Outside the 14-day window: find_dedup_candidate returns
        (None, False), so a new Job is created."""
        company = _make_company(
            identification_patterns=[{"patterns": [r"rekey", r"\d{3}-\d{3}-\d{4}"]}],
        )
        message = _make_message(content="Rekey at 123 N Main St, Chicago, IL 60601. 773-555-1212.")
        pending_dj = MagicMock()
        pending_dj.id = uuid4()
        new_job = MagicMock(spec=Job)
        new_job.id = uuid4()

        _mock_extraction_llm(monkeypatch, address="123 N Main St, Chicago, IL 60601")

        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.job_repo") as mock_job_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)
            mock_company_repo.get_all_active = AsyncMock(return_value=[company])
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)
            # Outside the 14-day window: the repo returns (None, False)
            mock_job_repo.find_dedup_candidate = AsyncMock(return_value=(None, False))
            mock_job_repo.create_job = AsyncMock(return_value=new_job)

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        # The 14-day window is anchored to first_message_at, not to "now".
        # If no candidate falls inside the window, no dedup happens.
        mock_job_repo.find_dedup_candidate.assert_awaited_once()
        create_kwargs = mock_job_repo.create_job.await_args.kwargs
        assert create_kwargs["is_duplicate"] is False
        assert create_kwargs["duplicate_of"] is None
        assert DEDUP_WINDOW_DAYS == 14

    @pytest.mark.anyio
    async def test_classify_message_empty_content_marks_not_a_job(
        self, monkeypatch, mock_db_session
    ):
        """Empty messages never get extracted or deduped — they short-
        circuit to NOT_A_JOB with an explanatory error."""
        message = _make_message(content="")
        pending_dj = MagicMock()
        pending_dj.id = uuid4()

        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.job_repo") as mock_job_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        # No LLM was called, no dedup lookup, no Job created
        mock_job_repo.find_dedup_candidate.assert_not_called()
        mock_job_repo.create_job.assert_not_called()
        update_kwargs = mock_dj_repo.update_dispatch_job.await_args.kwargs
        assert update_kwargs["classification_status"] == ClassificationStatus.NOT_A_JOB.value
        assert "Empty" in (update_kwargs.get("classification_error") or "")

    @pytest.mark.anyio
    async def test_classify_message_failed_company_identification(
        self, monkeypatch, mock_db_session
    ):
        """If no regex pattern matches AND the AI fallback returns no
        match, the job is FAILED with a clear error."""
        message = _make_message(
            content="Lockout at 123 N Main St, Chicago, IL 60601. 773-555-1212."
        )
        pending_dj = MagicMock()
        pending_dj.id = uuid4()
        other_company = _make_company(
            name="Other",
            identification_patterns=[{"patterns": [r"this-will-never-match"]}],
        )

        # Make the AI fallback also return None by mocking the chain.
        with (
            patch("app.services.classification.company_repo") as mock_company_repo,
            patch("app.services.classification.dispatch_job_repo") as mock_dj_repo,
        ):
            mock_company_repo.get_by_phone_number = AsyncMock(return_value=None)
            mock_company_repo.get_all_active = AsyncMock(return_value=[other_company])
            mock_dj_repo.create_dispatch_job = AsyncMock(return_value=pending_dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=pending_dj)

            # Also make the AI company-classify LLM return no match.
            structured = MagicMock()
            structured.ainvoke = AsyncMock(
                return_value=MagicMock(company_name=None, confidence=0.0, reasoning="n/a")
            )
            instance = MagicMock()
            instance.with_structured_output.return_value = structured
            monkeypatch.setattr(
                "app.services.classification.ChatOpenAI", MagicMock(return_value=instance)
            )

            svc = JobClassificationService(mock_db_session)
            await svc.classify_message(message)

        update_kwargs = mock_dj_repo.update_dispatch_job.await_args.kwargs
        assert update_kwargs["classification_status"] == ClassificationStatus.FAILED.value
        assert "No company matched" in (update_kwargs.get("classification_error") or "")
