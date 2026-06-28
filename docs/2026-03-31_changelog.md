# Changelog — OpenPhone Integration & Dispatch Classification

**Date:** 2026-03-31
**Branch:** `main` (uncommitted)

## Summary

This update transforms the project from a generic FastAPI template into **Dispatch Chicago** — an application that receives messages from OpenPhone (via the Quo API), classifies them into dispatch jobs using a hybrid regex + AI pipeline, and exposes the classified data through REST endpoints.

---

## New Dependencies

| Package | Purpose |
|---------|---------|
| `httpx>=0.27.0` | Async HTTP client for the Quo/OpenPhone API |
| `playwright>=1.49.0` | Browser automation (persistent Chromium context for future automation tasks) |

---

## New Features

### 1. OpenPhone (Quo API) Integration

- **HTTP Client** (`app/clients/openphone.py`): Async client wrapping the Quo REST API — phone numbers, users, messages, conversations, and webhook management.
- **Webhook Receiver** (`POST /webhooks/openphone`): Public endpoint that receives webhook events from Quo, verifies HMAC-SHA256 signatures, and processes messages in a background task.
- **API Proxy** (`/openphone/*`): JWT-protected endpoints that proxy requests to the Quo API (list/get phone numbers, users, messages, conversations, webhooks).
- **Incoming Messages Store**: All webhook messages are persisted to the `incoming_messages` table with full raw payloads for auditing.

### 2. Dispatch Job Classification Pipeline

A three-tier classification engine (`app/services/classification.py`) that runs on every incoming message:

| Tier | Method | Description |
|------|--------|-------------|
| 1 | Phone number lookup | Matches sender phone against company records (last-10-digit normalization) |
| 2 | Regex matching | Tests all active company pattern groups (AND within group, OR across groups) |
| 3 | AI fallback | Uses `ChatOpenAI` with structured output to identify the company (confidence >= 0.5) |

If a company is identified, a second AI call extracts **9 structured fields**: address, job_type, total, parts, payment_method, tech_name, car_make, car_model, car_year.

### 3. Company Management

- **Company Model** (`companies` table): Stores company name, display name, identification patterns (JSONB), phone numbers (JSONB), and pattern type (regex/ai).
- **Seed Command** (`seed-companies`): CLI command that loads 38 company definitions from `app/data/companies.json` into the database.
- **Company Data** (`app/data/companies.json`): 38 companies with regex patterns for identification (e.g., "911 Locksmith" with 15 sub-brand pattern groups).

### 4. Dispatch Jobs API

- `GET /dispatch/jobs` — List classified jobs with pagination and filters (status, company_id)
- `GET /dispatch/jobs/{job_id}` — Get a single job by UUID
- `POST /dispatch/jobs/{job_id}/reclassify` — Re-run the classification pipeline on a job

### 5. Browser Automation

- **Browser Manager** (`app/browser/manager.py`): Singleton managing a persistent Playwright Chromium context that survives app restarts (cookies, localStorage, cache preserved to disk).
- Configurable via `BROWSER_ENABLED`, `BROWSER_HEADLESS`, `BROWSER_CHANNEL`, `BROWSER_USER_DATA_DIR`.
- Started/stopped during the FastAPI app lifespan.

---

## New Files (22)

### Models
- `backend/app/db/models/openphone.py` — `IncomingMessage` model
- `backend/app/db/models/company.py` — `Company` model with `PatternType` enum
- `backend/app/db/models/dispatch_job.py` — `DispatchJob` model with `ClassificationStatus` enum

### Schemas
- `backend/app/schemas/openphone.py` — Quo API response schemas + webhook payload + send message request
- `backend/app/schemas/company.py` — `CompanyRead`, `CompanyList`
- `backend/app/schemas/dispatch_job.py` — `DispatchJobRead`, `DispatchJobList`, `JobExtraction`, `CompanyClassification`

### Repositories
- `backend/app/repositories/openphone.py` — CRUD for `incoming_messages`
- `backend/app/repositories/company.py` — CRUD for `companies` (including phone number matching, bulk create)
- `backend/app/repositories/dispatch_job.py` — CRUD for `dispatch_jobs` (with partial update logic)

### Services
- `backend/app/services/openphone.py` — Webhook processing + Quo API proxy logic
- `backend/app/services/classification.py` — Three-tier classification + AI extraction engine
- `backend/app/services/dispatch_job.py` — Dispatch job business logic + reclassification

### API Routes
- `backend/app/api/routes/v1/openphone.py` — Webhook receiver + proxy + incoming messages endpoints
- `backend/app/api/routes/v1/dispatch_jobs.py` — Dispatch job listing, detail, reclassify

### Clients
- `backend/app/clients/openphone.py` — Async HTTP client for Quo REST API

### Browser
- `backend/app/browser/__init__.py` — Package marker
- `backend/app/browser/manager.py` — Persistent Playwright browser manager

### Commands
- `backend/app/commands/seed_companies.py` — CLI command to seed company data

### Data
- `backend/app/data/companies.json` — 38 company definitions with identification patterns

### Core
- `backend/app/core/webhook.py` — HMAC-SHA256 signature verification for OpenPhone webhooks

### Documentation
- `docs/index.md` — Documentation index page

---

## Modified Files (14)

| File | Change |
|------|--------|
| `.gitignore` | Added `browser_data/` to ignore list |
| `AGENTS.md` | Updated project name to "Dispatch Chicago", added docs structure, documentation conventions |
| `CLAUDE.md` | Updated project name, added documentation conventions, browser env vars |
| `backend/.env.example` | Added `OPENPHONE_API_KEY`, `OPENPHONE_WEBHOOK_SECRET`, `OPENPHONE_BASE_URL` |
| `backend/alembic/env.py` | Imported new models for Alembic autogeneration |
| `backend/app/api/deps.py` | Added `OpenPhoneSvc`, `BrowserPage` dependencies |
| `backend/app/api/routes/v1/__init__.py` | Registered `openphone` and `dispatch_jobs` routers |
| `backend/app/core/config.py` | Added OpenPhone + Browser settings |
| `backend/app/db/models/__init__.py` | Exported new models and enums |
| `backend/app/main.py` | Added browser manager start/stop in lifespan |
| `backend/app/repositories/__init__.py` | Registered new repository modules |
| `backend/app/services/__init__.py` | Registered new service modules |
| `backend/pyproject.toml` | Added `httpx` and `playwright` dependencies |
| `backend/uv.lock` | Updated lock file |

---

## Data Flow

```
OpenPhone (Quo)
    │
    ▼ webhook event
POST /webhooks/openphone  (verify HMAC signature)
    │
    ▼ background task (separate DB session)
OpenPhoneService.process_webhook()
    │ persist IncomingMessage
    ▼
JobClassificationService.classify_message()
    │ create DispatchJob (PENDING)
    ├─ Tier 1: phone number match → company
    ├─ Tier 2: regex pattern match → company
    ├─ Tier 3: AI classification → company
    │
    ▼ if company found
AI field extraction (address, job_type, total, etc.)
    │
    ▼ update DispatchJob (CLASSIFIED)
```
