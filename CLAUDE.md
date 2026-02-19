# CLAUDE.md

## Project Overview

**agents_bots** - FastAPI application generated with [Full-Stack FastAPI + Next.js Template](https://github.com/vstorm-co/full-stack-fastapi-nextjs-llm-template).

**Stack:** FastAPI + Pydantic v2, PostgreSQL (async), JWT auth, LangChain, Next.js 15

## Commands

```bash
# Backend
cd backend
uv run uvicorn app.main:app --reload --port 8888
pytest
ruff check . --fix && ruff format .

# Database
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "Description"

# Frontend
cd frontend
bun dev
bun test

# Docker
docker compose up -d
```

## Project Structure

```
backend/app/
├── api/routes/v1/    # HTTP endpoints
├── services/         # Business logic
├── repositories/     # Data access
├── schemas/          # Pydantic models
├── db/models/        # Database models
├── core/config.py    # Settings
├── agents/           # AI agents
└── commands/         # CLI commands
```

## Key Conventions

- Use `db.flush()` in repositories (not `commit`)
- Services raise domain exceptions (`NotFoundError`, `AlreadyExistsError`)
- Schemas: separate `Create`, `Update`, `Response` models
- Commands auto-discovered from `app/commands/`

## Where to Find More Info

Before starting complex tasks, read relevant docs:
- **Architecture details:** `docs/architecture.md`
- **Adding features:** `docs/adding_features.md`
- **Testing guide:** `docs/testing.md`
- **Code patterns:** `docs/patterns.md`

## Environment Variables

Key variables in `.env`:
```bash
ENVIRONMENT=local
POSTGRES_HOST=localhost
POSTGRES_PASSWORD=secret
SECRET_KEY=change-me-use-openssl-rand-hex-32
OPENAI_API_KEY=sk-...
LOGFIRE_TOKEN=your-token
```
