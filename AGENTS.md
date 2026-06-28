# AGENTS.md

This file provides guidance for AI coding agents (Codex, Copilot, Cursor, Zed, OpenCode).

## Project Overview

**Dispatch Chicago** - An application that integrates with messaging platforms (Twilio/OpenPhone) using their free tier to receive messages from different companies. It classifies incoming messages, monitors job status in real-time, and generates metrics for dispatch operations.

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

docs/
├── index.md          # Documentation index
├── guides/           # Project-specific guides
└── *.md              # Core documentation
```

## Key Conventions

- `db.flush()` in repositories, not `commit()`
- Services raise `NotFoundError`, `AlreadyExistsError`
- Separate `Create`, `Update`, `Response` schemas

## Documentation Conventions

- All `.md` files in `docs/` must use the naming format: `YYYY-MM-DD_name.md` (e.g., `2026-03-31_setup_guide.md`)
- Every new document must be added to `docs/index.md`
- Project-specific guides go in `docs/guides/` (same naming convention)

## More Info

- `docs/index.md` - Full documentation index
- `docs/architecture.md` - Architecture details
- `docs/adding_features.md` - How to add features
- `docs/testing.md` - Testing guide
- `docs/patterns.md` - Code patterns
