# Adding New Features

## Adding a New API Endpoint

1. **Create schema** in `schemas/`
   ```python
   # schemas/item.py
   class ItemCreate(BaseModel):
       name: str
       description: str | None = None

   class ItemResponse(BaseModel):
       id: UUID
       name: str
       created_at: datetime
   ```

2. **Create model** in `db/models/` (if new entity)
   ```python
   # db/models/item.py
   class Item(Base):
       __tablename__ = "items"
       id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
       name: Mapped[str] = mapped_column(String(255))
   ```

3. **Create repository** in `repositories/`
   ```python
   # repositories/item.py
   class ItemRepository:
       async def create(self, db: AsyncSession, **kwargs) -> Item:
           item = Item(**kwargs)
           db.add(item)
           await db.flush()
           await db.refresh(item)
           return item
   ```

4. **Create service** in `services/`
   ```python
   # services/item.py
   class ItemService:
       def __init__(self, db: AsyncSession):
           self.db = db
           self.repo = ItemRepository()

       async def create(self, item_in: ItemCreate) -> Item:
           return await self.repo.create(self.db, **item_in.model_dump())
   ```

5. **Create route** in `api/routes/v1/`
   ```python
   # api/routes/v1/items.py
   router = APIRouter(prefix="/items", tags=["items"])

   @router.post("/", response_model=ItemResponse, status_code=201)
   async def create_item(
       item_in: ItemCreate,
       db: AsyncSession = Depends(get_db),
   ):
       service = ItemService(db)
       return await service.create(item_in)
   ```

6. **Register route** in `api/routes/v1/__init__.py`
   ```python
   from .items import router as items_router
   api_router.include_router(items_router)
   ```

## Adding a Custom CLI Command

Commands are auto-discovered from `app/commands/`.

```python
# app/commands/my_command.py
from app.commands import command, success, error
import click

@command("my-command", help="Description of what this does")
@click.option("--name", "-n", required=True, help="Name parameter")
def my_command(name: str):
    # Your logic here
    success(f"Done: {name}")
```

Run with: `uv run agents_bots cmd my-command --name test`

## Adding an AI Agent Tool (LangChain)

```python
# app/agents/langchain_assistant.py
from langchain.tools import tool

@tool
def my_tool(param: str) -> dict:
    """Tool description for LLM - be specific about what it does."""
    result = some_operation(param)
    return {"result": result}
```

## Adding a Database Migration

```bash
# Create migration
uv run alembic revision --autogenerate -m "Add items table"

# Apply migration
uv run alembic upgrade head

# Or use CLI
uv run agents_bots db migrate -m "Add items table"
uv run agents_bots db upgrade
```
