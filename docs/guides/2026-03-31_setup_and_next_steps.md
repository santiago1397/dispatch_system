# Setup Guide & Next Steps

**Date:** 2026-03-31

> **⚠️ SUPERSEDED 2026-06-08.** This doc is from the project's first
> day. The current canonical setup guide is
> [2026-06-04_whatsapp_extension.md](2026-06-04_whatsapp_extension.md).
> The seed workflow (companies.json generation) is now in
> [2026-06-08_seeding_and_reclassification.md](2026-06-08_seeding_and_reclassification.md).
>
> The 2026-03-31 version of this doc has several **stale commands** that
> will not work on a fresh checkout:
>
> | Stale | Current |
> |---|---|
> | `cp backend/.env.example backend/.env` | No `.env.example` exists. Write `.env` by hand — see the 2026-06-04 guide for the minimal template. |
> | `uv run alembic revision --autogenerate` then `alembic upgrade head` | No migrations in `alembic/versions/`. Bootstrap with `Base.metadata.create_all` — see the 2026-06-04 guide. |
> | `uv run python -m app.commands seed-companies` | `uv run agents_bots cmd seed-companies` (and the new pipeline is notebook → `generate_companies_seed.py` → `companies.json` → this command, not direct seeding). |
>
> OpenPhone-only setup steps further down (webhook config, ngrok) are
> still useful if you're wiring up an OpenPhone source alongside the
> WhatsApp one. The "Next Steps" section is historical and the
> "Priority" items have been either completed or absorbed into the new
> guides.

---

## Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- PostgreSQL 15+ running locally or remotely
- An OpenPhone account with API access (Quo API)
- OpenAI API key (for AI classification/extraction)

---

## Initial Setup

### 1. Environment Variables

Copy the example env and fill in your values:

```bash
cp backend/.env.example backend/.env
```

Required new variables:

```bash
# OpenPhone (Quo API)
OPENPHONE_API_KEY=your-openphone-api-key
OPENPHONE_WEBHOOK_SECRET=your-webhook-secret   # obtained after creating a webhook
OPENPHONE_BASE_URL=https://api.openphone.com/v1

# OpenAI (for AI classification)
OPENAI_API_KEY=sk-...

# Browser automation (optional, set to false if not needed)
BROWSER_ENABLED=false
BROWSER_HEADLESS=true
```

### 2. Install Dependencies

```bash
cd backend
uv sync
```

If you plan to use Playwright browser automation:

```bash
uv run playwright install chromium
```

### 3. Database Migration

Generate and apply the migration for the new tables:

```bash
cd backend
uv run alembic revision --autogenerate -m "add openphone company dispatch_job tables"
uv run alembic upgrade head
```

### 4. Seed Company Data

Load the 38 company definitions into the database:

```bash
cd backend
uv run python -m app.commands seed-companies
```

Options:
- `--clear` — Delete all existing companies before seeding
- `--dry-run` — Preview what would be created without making changes

---

## Configuring OpenPhone Webhooks

### Step 1: Create a Webhook via the API

Use the proxy endpoint (after starting the app):

```bash
# Start the app first
uv run uvicorn app.main:app --reload --port 8888

# Create a webhook pointing to your local server
# (use ngrok or similar for external access)
curl -X POST http://localhost:8888/api/v1/openphone/webhooks \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-ngrok-url.ngrok.io/api/v1/openphone/webhooks",
    "events": ["message.received"]
  }'
```

The response will include a `key` field — set this as `OPENPHONE_WEBHOOK_SECRET` in your `.env`.

### Step 2: Expose Your Local Server

For local development, use ngrok to expose your webhook endpoint:

```bash
ngrok http 8888
```

Use the ngrok HTTPS URL as your webhook URL.

### Step 3: Test the Webhook

Send a test message to your OpenPhone number and verify:
1. The message appears in `GET /api/v1/openphone/incoming`
2. A dispatch job appears in `GET /api/v1/dispatch/jobs`

---

## API Endpoints Reference

### OpenPhone Webhook (Public)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/openphone/webhooks` | None | Receives webhook events from Quo |

### OpenPhone Proxy (JWT Required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/openphone/phone-numbers` | List phone numbers |
| POST | `/api/v1/openphone/phone-numbers` | Create/get phone number |
| GET | `/api/v1/openphone/phone-numbers/{id}` | Get phone number |
| GET | `/api/v1/openphone/users` | List users |
| POST | `/api/v1/openphone/users` | Create/get user |
| GET | `/api/v1/openphone/users/{id}` | Get user |
| GET | `/api/v1/openphone/messages` | List messages |
| POST | `/api/v1/openphone/messages` | Send a message |
| GET | `/api/v1/openphone/messages/{id}` | Get message |
| GET | `/api/v1/openphone/conversations` | List conversations |
| GET | `/api/v1/openphone/webhooks` | List webhooks |
| POST | `/api/v1/openphone/webhooks` | Create webhook |
| GET | `/api/v1/openphone/webhooks/{id}` | Get webhook |
| DELETE | `/api/v1/openphone/webhooks/{id}` | Delete webhook |

### Incoming Messages (JWT Required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/openphone/incoming` | List persisted incoming messages |
| GET | `/api/v1/openphone/incoming/{id}` | Get a single incoming message |

### Dispatch Jobs (JWT Required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/dispatch/jobs` | List dispatch jobs (filter by status, company_id) |
| GET | `/api/v1/dispatch/jobs/{id}` | Get a single dispatch job |
| POST | `/api/v1/dispatch/jobs/{id}/reclassify` | Re-run classification pipeline |

---

## Next Steps

### Priority 1 — Get It Running

- [ ] Fill in `.env` with real OpenPhone and OpenAI credentials
- [ ] Run `alembic revision --autogenerate` and `alembic upgrade head`
- [ ] Run `seed-companies` to populate company data
- [ ] Start the app and verify it boots without errors
- [ ] Expose via ngrok and configure the OpenPhone webhook
- [ ] Send a test message and verify end-to-end flow

### Priority 2 — Stabilize & Harden

- [ ] **Add error retry logic** in `classification.py` — if the OpenAI call fails, mark the job as `failed` with the error instead of crashing
- [ ] **Webhook idempotency** — currently deduplicates by `openphone_id`, but verify this handles Quo's retry behavior correctly
- [ ] **Rate limiting** — add rate limits on the webhook endpoint to prevent abuse
- [ ] **Logging** — add structured logging for the classification pipeline (which tier matched, AI confidence scores, extraction results)
- [ ] **Tests** — write integration tests for the webhook flow and unit tests for the classification tiers

### Priority 3 — Features

- [ ] **Frontend dashboard** — build a Next.js page showing classified jobs, filterable by company/status, with a reclassify button
- [ ] **Real-time updates** — use WebSocket or SSE to push new classified jobs to the frontend as they arrive
- [ ] **Metrics** — job volume per company, classification accuracy, average extraction confidence
- [ ] **Company CRUD** — add API endpoints to create/update/delete companies (currently only the seed command can do this)
- [ ] **Browser automation** — the Playwright infrastructure is in place but not yet used. Integrate it to automate actions on dispatch platforms
- [ ] **Notification system** — send alerts (via OpenPhone or another channel) when high-priority jobs are detected

### Priority 4 — Production Readiness

- [ ] **Docker setup** — update `docker-compose.yml` to include the new env vars and Playwright browser binaries
- [ ] **Database backups** — set up automated PostgreSQL backups
- [ ] **Monitoring** — connect the existing Logfire integration to track classification pipeline health
- [ ] **CI/CD** — add GitHub Actions for linting, testing, and deployment
- [ ] **Security audit** — review webhook signature verification (currently permissive when no secret is set — must be strict in production)
