"""Tests for repository layer."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.repositories.base import BaseRepository


class MockModel:
    """Mock SQLAlchemy model for testing."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid4())
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockCreateSchema(BaseModel):
    """Mock create schema."""

    name: str


class MockUpdateSchema(BaseModel):
    """Mock update schema."""

    name: str | None = None


class TestBaseRepository:
    """Tests for BaseRepository."""

    @pytest.fixture
    def repository(self):
        """Create a test repository."""
        return BaseRepository[MockModel, MockCreateSchema, MockUpdateSchema](MockModel)

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = MagicMock()
        session.get = AsyncMock()
        session.execute = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        session.delete = AsyncMock()
        return session

    @pytest.mark.anyio
    async def test_get_returns_model(self, repository, mock_session):
        """Test get returns a model by ID."""
        mock_obj = MockModel(name="test")
        mock_session.get.return_value = mock_obj

        result = await repository.get(mock_session, mock_obj.id)

        assert result == mock_obj
        mock_session.get.assert_called_once_with(MockModel, mock_obj.id)

    @pytest.mark.anyio
    async def test_get_returns_none_when_not_found(self, repository, mock_session):
        """Test get returns None when not found."""
        mock_session.get.return_value = None

        result = await repository.get(mock_session, uuid4())

        assert result is None

    # Note: test_get_multi_returns_list is skipped because it requires a real
    # SQLAlchemy model. The select() function cannot work with a mock class.
    # For proper integration testing, use actual SQLAlchemy models with a test DB.

    @pytest.mark.anyio
    async def test_create_adds_and_returns_model(self, repository, mock_session):
        """Test create adds a new model."""
        create_data = MockCreateSchema(name="new item")

        # Mock the model creation
        async def refresh_side_effect(obj):
            obj.id = uuid4()

        mock_session.refresh.side_effect = refresh_side_effect

        result = await repository.create(mock_session, obj_in=create_data)

        assert result.name == "new item"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.anyio
    async def test_update_with_schema(self, repository, mock_session):
        """Test update with Pydantic schema."""
        db_obj = MockModel(name="old name")
        update_data = MockUpdateSchema(name="new name")

        result = await repository.update(mock_session, db_obj=db_obj, obj_in=update_data)

        assert result.name == "new name"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    @pytest.mark.anyio
    async def test_update_with_dict(self, repository, mock_session):
        """Test update with dictionary."""
        db_obj = MockModel(name="old name")
        update_data = {"name": "new name"}

        result = await repository.update(mock_session, db_obj=db_obj, obj_in=update_data)

        assert result.name == "new name"

    @pytest.mark.anyio
    async def test_delete_removes_and_returns_model(self, repository, mock_session):
        """Test delete removes and returns model."""
        mock_obj = MockModel(name="to delete")
        mock_session.get.return_value = mock_obj

        result = await repository.delete(mock_session, id=mock_obj.id)

        assert result == mock_obj
        mock_session.delete.assert_called_once_with(mock_obj)
        mock_session.flush.assert_called_once()

    @pytest.mark.anyio
    async def test_delete_returns_none_when_not_found(self, repository, mock_session):
        """Test delete returns None when not found."""
        mock_session.get.return_value = None

        result = await repository.delete(mock_session, id=uuid4())

        assert result is None
        mock_session.delete.assert_not_called()


class TestUserRepository:
    """Tests for user repository functions."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = MagicMock()
        session.execute = AsyncMock()
        return session

    @pytest.mark.anyio
    async def test_get_by_email(self, mock_session):
        """Test get_by_email returns user."""
        from app.repositories import user as user_repo

        mock_user = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute.return_value = mock_result

        result = await user_repo.get_by_email(mock_session, "test@example.com")

        assert result == mock_user

    @pytest.mark.anyio
    async def test_get_by_email_not_found(self, mock_session):
        """Test get_by_email returns None when not found."""
        from app.repositories import user as user_repo

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await user_repo.get_by_email(mock_session, "notfound@example.com")

        assert result is None
