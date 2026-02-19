"""Tests for items CRUD routes.

This module demonstrates testing patterns for CRUD endpoints.
You can use it as a template for testing your own endpoints.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.main import app


class MockItem:
    """Mock item for testing."""

    def __init__(
        self,
        id=None,
        title="Test Item",
        description="Test Description",
        is_active=True,
    ):
        self.id = id or uuid4()
        self.title = title
        self.description = description
        self.is_active = is_active
        self.created_at = datetime.now(UTC)
        self.updated_at = None


@pytest.fixture
def mock_item() -> MockItem:
    """Create a mock item."""
    return MockItem()


@pytest.fixture
def mock_items() -> list[MockItem]:
    """Create multiple mock items."""
    return [
        MockItem(title="Item 1", description="First item"),
        MockItem(title="Item 2", description="Second item"),
        MockItem(title="Item 3", description="Third item"),
    ]


@pytest.fixture
def mock_item_service(mock_item: MockItem, mock_items: list[MockItem]) -> MagicMock:
    """Create a mock item service."""
    service = MagicMock()
    service.get_by_id = AsyncMock(return_value=mock_item)
    service.get_multi = AsyncMock(return_value=mock_items)
    service.create = AsyncMock(return_value=mock_item)
    service.update = AsyncMock(return_value=mock_item)
    service.delete = AsyncMock(return_value=mock_item)
    return service


@pytest.fixture
async def client_with_mock_service(
    mock_item_service: MagicMock,
    mock_db_session,
) -> AsyncClient:
    """Client with mocked item service."""
    from httpx import ASGITransport

    from app.api.deps import get_item_service
    from app.db.session import get_db_session

    app.dependency_overrides[get_item_service] = lambda db=None: mock_item_service
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_create_item_success(client_with_mock_service: AsyncClient):
    """Test successful item creation."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/items",
        json={"title": "New Item", "description": "A new item"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Item"  # From mock


@pytest.mark.anyio
async def test_create_item_minimal(client_with_mock_service: AsyncClient):
    """Test item creation with minimal data (only required fields)."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/items",
        json={"title": "Minimal Item"},
    )
    assert response.status_code == 201


@pytest.mark.anyio
async def test_create_item_validation_error(client_with_mock_service: AsyncClient):
    """Test item creation with invalid data."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/items",
        json={},  # Missing required 'title' field
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_get_item_success(
    client_with_mock_service: AsyncClient,
    mock_item: MockItem,
):
    """Test successful item retrieval."""
    response = await client_with_mock_service.get(
        f"{settings.API_V1_STR}/items/{mock_item.id}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == mock_item.title
    assert data["description"] == mock_item.description


@pytest.mark.anyio
async def test_get_item_not_found(
    client_with_mock_service: AsyncClient,
    mock_item_service: MagicMock,
):
    """Test item retrieval when item doesn't exist."""
    from app.core.exceptions import NotFoundError

    mock_item_service.get_by_id = AsyncMock(side_effect=NotFoundError(message="Item not found"))
    response = await client_with_mock_service.get(
        f"{settings.API_V1_STR}/items/{uuid4()}",
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_update_item_success(
    client_with_mock_service: AsyncClient,
    mock_item: MockItem,
):
    """Test successful item update."""
    response = await client_with_mock_service.patch(
        f"{settings.API_V1_STR}/items/{mock_item.id}",
        json={"title": "Updated Title"},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_update_item_partial(
    client_with_mock_service: AsyncClient,
    mock_item: MockItem,
):
    """Test partial item update (only some fields)."""
    response = await client_with_mock_service.patch(
        f"{settings.API_V1_STR}/items/{mock_item.id}",
        json={"is_active": False},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_update_item_not_found(
    client_with_mock_service: AsyncClient,
    mock_item_service: MagicMock,
):
    """Test item update when item doesn't exist."""
    from app.core.exceptions import NotFoundError

    mock_item_service.update = AsyncMock(side_effect=NotFoundError(message="Item not found"))
    response = await client_with_mock_service.patch(
        f"{settings.API_V1_STR}/items/{uuid4()}",
        json={"title": "Updated"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_item_success(
    client_with_mock_service: AsyncClient,
    mock_item: MockItem,
):
    """Test successful item deletion."""
    response = await client_with_mock_service.delete(
        f"{settings.API_V1_STR}/items/{mock_item.id}",
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_delete_item_not_found(
    client_with_mock_service: AsyncClient,
    mock_item_service: MagicMock,
):
    """Test item deletion when item doesn't exist."""
    from app.core.exceptions import NotFoundError

    mock_item_service.delete = AsyncMock(side_effect=NotFoundError(message="Item not found"))
    response = await client_with_mock_service.delete(
        f"{settings.API_V1_STR}/items/{uuid4()}",
    )
    assert response.status_code == 404
