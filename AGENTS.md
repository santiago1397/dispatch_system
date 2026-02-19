# AGENTS.md

This file provides guidance for AI coding agents (Codex, Copilot, Cursor, Zed, OpenCode).

## Project Overview

**agents_bots** - FastAPI application.

**Stack:** FastAPI + Pydantic v2, PostgreSQL, JWT auth, Next.js 15

## Commands

```bash
# Run server
cd backend && uv run uvicorn app.main:app --reload

# Tests & lint
pytest
ruff check . --fix && ruff format .

# Migrations
uv run alembic upgrade head
```

## Project Structure

```
backend/app/
├── api/routes/v1/    # Endpoints
├── services/         # Business logic
├── repositories/     # Data access
├── schemas/          # Pydantic models
├── db/models/        # DB models
└── commands/         # CLI commands
```

## Key Conventions

- `db.flush()` in repositories, not `commit()`
- Services raise `NotFoundError`, `AlreadyExistsError`
- Separate `Create`, `Update`, `Response` schemas

## More Info

- `docs/architecture.md` - Architecture details
- `docs/adding_features.md` - How to add features
- `docs/testing.md` - Testing guide
- `docs/patterns.md` - Code patterns
