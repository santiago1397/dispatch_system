# Code Patterns

## Dependency Injection

Use FastAPI's `Depends()` for injecting dependencies:

```python
from app.api.deps import get_db, get_current_user

@router.get("/items")
async def list_items(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ItemService(db)
    return await service.get_multi()
```

Available dependencies in `app/api/deps.py`:
- `get_db` - Database session
- `get_current_user` - Authenticated user (raises 401 if not authenticated)
- `get_current_user_optional` - User or None

## Service Layer Pattern

Services contain business logic:

```python
class ItemService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ItemRepository()

    async def create(self, item_in: ItemCreate) -> Item:
        # Business validation
        if await self.repo.exists_by_name(self.db, item_in.name):
            raise AlreadyExistsError(message="Item already exists")

        # Create via repository
        return await self.repo.create(self.db, **item_in.model_dump())

    async def get_or_raise(self, id: UUID) -> Item:
        item = await self.repo.get_by_id(self.db, id)
        if not item:
            raise NotFoundError(message="Item not found", details={"id": str(id)})
        return item
```

## Repository Layer Pattern

Repositories handle data access only:

```python
class ItemRepository:
    async def get_by_id(self, db: AsyncSession, id: UUID) -> Item | None:
        return await db.get(Item, id)

    async def create(self, db: AsyncSession, **kwargs) -> Item:
        item = Item(**kwargs)
        db.add(item)
        await db.flush()  # Not commit! Let dependency manage transaction
        await db.refresh(item)
        return item

    async def get_multi(
        self, db: AsyncSession, skip: int = 0, limit: int = 100
    ) -> list[Item]:
        result = await db.execute(
            select(Item).offset(skip).limit(limit)
        )
        return list(result.scalars().all())
```

## Exception Handling

Use domain exceptions in services:

```python
from app.core.exceptions import NotFoundError, AlreadyExistsError, ValidationError

# In service
if not item:
    raise NotFoundError(
        message="Item not found",
        details={"id": str(id)}
    )

if await self.repo.exists_by_email(self.db, email):
    raise AlreadyExistsError(
        message="User with this email already exists"
    )
```

Exception handlers convert to HTTP responses automatically.

## Schema Patterns

Separate schemas for different operations:

```python
# Base with shared fields
class ItemBase(BaseModel):
    name: str
    description: str | None = None

# For creation (input)
class ItemCreate(ItemBase):
    pass

# For updates (all optional)
class ItemUpdate(BaseModel):
    name: str | None = None
    description: str | None = None

# For responses (with DB fields)
class ItemResponse(ItemBase):
    id: UUID
    created_at: datetime
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
```

## Frontend Patterns

### Authentication (HTTP-only cookies)

```typescript
import { useAuth } from '@/hooks/use-auth';

function Component() {
    const { user, isAuthenticated, login, logout } = useAuth();
}
```

### State Management (Zustand)

```typescript
import { useAuthStore } from '@/stores/auth-store';

const { user, setUser, logout } = useAuthStore();
```

### WebSocket Chat

```typescript
import { useChat } from '@/hooks/use-chat';

function ChatPage() {
    const { messages, sendMessage, isStreaming } = useChat();
}
```
