# CLAUDE.md

## Project Overview

**Dispatch Chicago** - An application that integrates with messaging platforms (Twilio/OpenPhone) using their free tier to receive messages from different companies. It classifies incoming messages, monitors job status in real-time, and generates metrics for dispatch operations.

**Stack:** FastAPI + Pydantic v2, PostgreSQL (async), JWT auth, LangChain, Next.js 15

## Commands

```bash
# Backend
cd backend
uv run uvicorn app.main:app --reload --port 8888
pytest
ruff check . --fix && ruff format .

# Database — NOTE: there are no alembic migrations checked in.
# The current schema was bootstrapped with Base.metadata.create_all
# (see "First-run gotchas" below). `alembic upgrade head` is a no-op.
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "Description"

# Companies seed — the dispatch notebook is the source of truth for
# the regex patterns. See docs/guides/2026-06-08_seeding_and_reclassification.md.
cd "../Python Scripts/dispatch_job_information/data_analytics_dispatch"
python generate_companies_seed.py
cd "../../web projects/dispatch_chicago/distpatch_bot/backend"
uv run agents_bots cmd seed-companies --clear

# Users + extension service-account seed — creates 1 superadmin +
# 1 operator + 1 "WhatsApp Extension" service account. The svc API
# key is printed on every run; paste it into the extension popup.
# Dev-only defaults:
#   admin:   admin@dispatch-chicago.com / admin123
#   user:    dispatch@example.com      / password123
#   svc key: random `sk_live_` + 32 hex (printed at seed time; paste into the extension popup)
# Override via --admin-email/--admin-password/--user-email/--user-password/--service-api-key.
uv run agents_bots cmd seed

# Frontend
cd frontend
bun dev
bun test

# Docker (full stack incl. Postgres)
docker compose up -d
```

## First-run gotchas (2026-06-05)

The project was first run end-to-end on 2026-06-05. Two latent template
bugs had to be fixed; both are now in the code, but if you `git diff`
later and see `(str, StrEnum)` patterns reappear, **remove the redundant
`str,`** — `enum.StrEnum` already inherits from `str` and listing both
as bases raises `TypeError: Cannot create a consistent method resolution
order (MRO) for bases str, StrEnum` on Python 3.11+. Affected files:
- `backend/app/db/models/company.py` — `PatternType`
- `backend/app/db/models/dispatch_job.py` — `ClassificationStatus`

Other first-run facts that may surprise you:

- **No alembic migrations exist.** `backend/alembic/versions/` is empty.
  The schema was built with `Base.metadata.create_all`. The first
  `alembic revision --autogenerate` will see the entire current schema
  as a brand-new baseline — handle it carefully (or use `--autogenerate`
  with a manual baseline revision first).
- **No `.env.example` exists.** The template's docs reference one, but
  only `backend/.env` is in the repo. Create your own from the env-var
  list below.
- **The template-generated README is the upstream one.** It describes
  template capabilities (LangChain, Logfire, Sentry, etc.) that may or
  may not be configured in this project. Trust `CLAUDE.md` and `docs/`
  over the README for project-specific truth.

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

## Documentation Conventions

- All `.md` files in `docs/` must follow the naming format: `YYYY-MM-DD_name.md` (e.g., `2026-03-31_setup_guide.md`)
- Each new document must be added to `docs/index.md` for discoverability
- Project-specific guides go in `docs/guides/` (same naming convention)
- See `docs/index.md` for the full list of available documentation

## Extension debugging

The WhatsApp scraper Chrome extension (`dispatch_extension/`) has a
**collapsible Activity log** in its popup. Every meaningful event in
the service worker and content script is logged with a timestamp,
severity, source tag, event name, and structured data, persisted to
`chrome.storage.local` and polled every 2s. **Check this first** when
the extension is connected but misbehaving — `SCRAPER_READY`,
`DOM_HEALTH_CHECK`, `BATCH_*`, `FLUSH_OK` / `FLUSH_FAILED`,
`HANDLER_FAILED` will tell you exactly where the chain broke without
opening Chrome DevTools.

## Where to Find More Info

Before starting complex tasks, read relevant docs:
- **Documentation index:** `docs/index.md`
- **Architecture details:** `docs/architecture.md`
- **Adding features:** `docs/adding_features.md`
- **Testing guide:** `docs/testing.md`
- **Code patterns:** `docs/patterns.md`
- **Seeding companies & reclassifying:** `docs/guides/2026-06-08_seeding_and_reclassification.md`
- **WhatsApp extension setup:** `docs/guides/2026-06-04_whatsapp_extension.md`

## Environment Variables

There is **no `.env.example` checked in.** Create `backend/.env` from the
keys below. For local dev against a Postgres 18 already running on
`localhost:5432` with default `postgres`/`postgres` creds:

```bash
ENVIRONMENT=local
DEBUG=true
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=dispatch_test
SECRET_KEY=local-dev-secret-do-not-use-in-production-7f3a9b2c1d4e5f6a
# Optional — leave blank to disable the respective integration.
# WITHOUT OPENAI_API_KEY, every job that gets past the regex stage
# fails with "api_key client option must be set".
OPENAI_API_KEY=
LOGFIRE_TOKEN=
```

If you change `POSTGRES_DB` to a fresh name, bootstrap the schema once
with `Base.metadata.create_all` (see
`docs/guides/2026-06-04_whatsapp_extension.md` for the snippet). The DB
name in `.env` and the actual database must match — the CLI does not
auto-create the database, only its tables.
