# Template Structure Guide

This document describes the **complete structure** of the agents_bots template (Full-Stack FastAPI + Next.js for AI/LLM applications). Use it to navigate the codebase and understand where to add or change code.

---

## Root Level

| Item | Description |
|------|-------------|
| `README.md` | Main project documentation, quick start, features, manual commands |
| `CLAUDE.md` | AI assistant (Claude) project context and commands |
| `AGENTS.md` | AI coding agents (Cursor, Copilot, etc.) overview and conventions |
| `Makefile` | Shortcuts for install, run, test, db, Docker (see `make help`) |
| `docker-compose.yml` | Full stack: backend app, PostgreSQL, optional frontend |
| `docker-compose.prod.yml` | Production Docker setup |
| `.env.prod.example` | Example production environment variables |
| `.gitignore` | Ignored files (env, cache, IDE, etc.) |

---

## Backend (`backend/`)

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Python deps, project config, CLI entry point |
| `uv.lock` | Locked dependency versions (uv) |
| `alembic.ini` | Alembic migration config |
| `alembic/versions/` | Database migration scripts |
| `Dockerfile` | Backend container image |
| `.env.example` | Example env vars (copy to `.env`) |
| `tests/` | Pytest suite (API, services, repos, agents, etc.) |
| `cli/` | Project CLI (`agents_bots` commands) |
| `app/` | Application code (see below) |

### Backend app (`backend/app/`)

| Directory | Purpose |
|-----------|---------|
| `main.py` | FastAPI app, lifespan, middleware, router mount |
| `api/` | HTTP layer: routes, deps, exception handlers, versioning |
| `api/routes/v1/` | Versioned API: health, auth, users, items, sessions, etc. |
| `api/deps.py` | Dependency injection (DB session, current user) |
| `core/` | Config, security (JWT), CSRF, sanitize, exceptions |
| `db/` | Database: base, session, models |
| `db/models/` | SQLAlchemy models (user, session, conversation, item) |
| `schemas/` | Pydantic request/response models |
| `repositories/` | Data access (user, session, conversation, item) |
| `services/` | Business logic (user, session, conversation) |
| `agents/` | AI agents, tools, prompts |
| `agents/tools/` | Agent tools (e.g. datetime) |
| `commands/` | Django-style CLI commands (example, seed, etc.) |
| `pipelines/` | Pipeline/flow logic if present |

---

## Frontend (`frontend/`)

| Path | Purpose |
|------|---------|
| `package.json` | Node/bun deps and scripts |
| `next.config.ts` | Next.js config |
| `Dockerfile` | Frontend container image |
| `.env.example` | Example frontend env |
| `e2e/` | Playwright E2E tests |
| `src/` | Source code (see below) |

### Frontend source (`frontend/src/`)

| Directory | Purpose |
|-----------|---------|
| `app/` | Next.js App Router: layout, pages, API routes |
| `app/(auth)/` | Auth routes: login, register |
| `app/(dashboard)/` | Dashboard: chat, profile |
| `app/api/` | API route handlers (auth, health, conversations) |
| `components/` | React components (auth, chat, layout, theme, ui) |
| `hooks/` | useAuth, useChat, useWebSocket, useLocalChat |
| `lib/` | API client, utils |
| `stores/` | Zustand stores (auth, chat, conversation, sidebar) |
| `types/` | TypeScript types (api, auth, chat) |

---

## Documentation (`docs/`)

| Document | Description |
|----------|-------------|
| `structure_guide.md` | **This file** – full template structure |
| `architecture.md` | Repository + Service layers, request flow |
| `adding_features.md` | How to add endpoints, CLI commands, migrations |
| `patterns.md` | DI, services, repos, schemas, exceptions |
| `testing.md` | Running tests, fixtures, frontend/E2E |

---

## Quick Reference: Where to Put Things

| You want to… | Add or edit in… |
|--------------|------------------|
| New API endpoint | `backend/app/api/routes/v1/`, then register in `__init__.py` |
| New DB model | `backend/app/db/models/`, then migration |
| New schema (request/response) | `backend/app/schemas/` |
| New repository | `backend/app/repositories/` |
| New service | `backend/app/services/` |
| New CLI command | `backend/app/commands/` (auto-discovered) |
| New agent tool | `backend/app/agents/tools/` |
| New frontend page | `frontend/src/app/` (App Router) |
| New React component | `frontend/src/components/` |
| New hook or store | `frontend/src/hooks/` or `frontend/src/stores/` |

---

## Uploading This Template to GitHub

See **[GITHUB_UPLOAD.md](../GITHUB_UPLOAD.md)** in the project root for step-by-step instructions to create a repo and push this template.

---

## Related Docs

- **Architecture & layers:** [docs/architecture.md](architecture.md)
- **Adding features:** [docs/adding_features.md](adding_features.md)
- **Code patterns:** [docs/patterns.md](patterns.md)
- **Testing:** [docs/testing.md](testing.md)
- **Root commands & setup:** [README.md](../README.md) and [CLAUDE.md](../CLAUDE.md)
